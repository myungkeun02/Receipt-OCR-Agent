#!/usr/bin/env python3
"""
🧾 Simple Receipt Processor

심플한 단일 API 엔드포인트로 영수증 처리
- Naver OCR → LLM 데이터 추출 → DB 패턴 분석 → 최종 판단
"""

import os
import json
import hashlib
import logging
import mysql.connector
import redis
import requests
import base64
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from flask import Flask, request, jsonify
from flask_restx import Api, Resource, fields, reqparse
from flask_cors import CORS
from werkzeug.datastructures import FileStorage
from openai import OpenAI
from dotenv import load_dotenv
import re

# 환경변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Flask 앱 설정
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

# CORS 설정 - 다른 포트의 웹서버에서 접근 허용
CORS(app, resources={
    r"/process": {"origins": "*"},  # 모든 도메인에서 /process 엔드포인트 접근 허용
    r"/health": {"origins": "*"}    # 모든 도메인에서 /health 엔드포인트 접근 허용
})

# Flask-RestX 설정
api = Api(
    app,
    version='2.0',
    title='🧾 Simple Receipt Processor',
    description='심플한 영수증 자동 처리 API',
    doc='/'
)

# API 모델 정의
receipt_model = api.model('Receipt', {
    'image': fields.Raw(required=True, description='영수증 이미지 파일')
})

# 파일 업로드를 위한 reqparse 정의
upload_parser = api.parser()
upload_parser.add_argument('image', location='files', type=FileStorage, required=True, help='영수증 이미지 파일 (JPG, PNG)')

response_model = api.model('ReceiptResponse', {
    'success': fields.Boolean(description='처리 성공 여부'),
    'data': fields.Raw(description='추출된 영수증 데이터 (amount, usageDateTime, usageLocation, accountCategory, description)'),
    'reasoning': fields.Raw(description='AI 판단 과정 및 추론 근거 (5단계 상세 분석)'),
    'processing_time': fields.String(description='처리 소요 시간'),
    'cache_used': fields.Boolean(description='캐시 사용 여부')
})

# 환경변수 설정
class Config:
    # OCR API
    CLOVA_OCR_API_KEY = os.getenv('CLOVA_OCR_API_KEY')
    CLOVA_OCR_ENDPOINT = os.getenv('CLOVA_OCR_ENDPOINT')
    
    # LLM API
    OPENAI_API_KEY = os.getenv('LLM_API_KEY')
    
    # Database
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', 3306))
    DB_USER = os.getenv('DB_USER')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME')

# Redis 클라이언트
try:
    redis_client = redis.Redis(host='localhost', port=6379, db=1, decode_responses=True)
    redis_client.ping()
    logging.info("✅ Redis 연결 성공")
except:
    redis_client = None
    logging.warning("❌ Redis 연결 실패 - 캐시 없이 동작")

# OpenAI 클라이언트
openai_client = OpenAI(api_key=Config.OPENAI_API_KEY) if Config.OPENAI_API_KEY else None

class ReceiptProcessor:
    """영수증 처리 메인 클래스"""
    
    def __init__(self):
        self.config = Config()
        
    def get_image_hash(self, image_data: bytes) -> str:
        """이미지 SHA-256 해시 생성"""
        return hashlib.sha256(image_data).hexdigest()
    
    def get_redis_cache(self, key: str) -> Optional[Dict]:
        """Redis 캐시 조회"""
        if not redis_client:
            return None
        
        try:
            cached_data = redis_client.get(key)
            if cached_data:
                return json.loads(cached_data)
        except Exception as e:
            logging.warning(f"Redis 조회 실패: {e}")
        
        return None
    
    def set_redis_cache(self, key: str, data: Dict, expire_seconds: int = 3600):
        """Redis 캐시 저장"""
        if not redis_client:
            return
        
        try:
            redis_client.setex(key, expire_seconds, json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logging.warning(f"Redis 저장 실패: {e}")
    
    def call_naver_ocr(self, image_data: bytes) -> Dict:
        """네이버 CLOVA OCR API 호출"""
        if not self.config.CLOVA_OCR_API_KEY:
            return {"error": "CLOVA OCR API 키가 설정되지 않았습니다"}
        
        try:
            # 이미지를 base64로 인코딩
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            # API 요청 데이터
            request_data = {
                "images": [{
                    "format": "jpg",
                    "name": "receipt",
                    "data": image_base64
                }],
                "requestId": f"receipt_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "version": "V2",
                "timestamp": int(datetime.now().timestamp() * 1000)
            }
            
            # API 헤더
            headers = {
                'X-OCR-SECRET': self.config.CLOVA_OCR_API_KEY,
                "Content-Type": "application/json"
            }
            
            # API 호출
            response = requests.post(
                self.config.CLOVA_OCR_ENDPOINT,
                headers=headers,
                json=request_data,
                timeout=30
            )
            
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            logging.error(f"네이버 OCR API 호출 실패: {e}")
            return {"error": str(e)}
    
    def extract_text_from_ocr(self, ocr_result: Dict) -> str:
        """OCR 결과에서 텍스트 추출"""
        try:
            text_blocks = []
            
            if 'images' in ocr_result and ocr_result['images']:
                image = ocr_result['images'][0]
                if 'fields' in image:
                    for field in image['fields']:
                        if 'inferText' in field:
                            text_blocks.append(field['inferText'])
            
            return '\n'.join(text_blocks)
            
        except Exception as e:
            logging.error(f"OCR 텍스트 추출 실패: {e}")
            return ""
    
    def extract_data_with_llm(self, ocr_text: str) -> Dict:
        """OCR 텍스트에서 구조화된 데이터 추출"""
        cache_key = f"receipt:llm:{hashlib.md5(ocr_text.encode()).hexdigest()}"
        
        # Redis 캐시 확인
        cached_result = self.get_redis_cache(cache_key)
        if cached_result:
            logging.info("✅ LLM 추출 캐시 적중")
            return cached_result
        
        try:
            prompt = f"""
다음 영수증 OCR 텍스트에서 정확한 정보를 추출해주세요:

"{ocr_text}"

다음 JSON 형식으로만 응답해주세요:
{{
    "amount": 숫자형태의_총금액,
    "rawDateTime": "원본_날짜시간_텍스트",
    "usageLocation": "상점명_또는_사용처"
}}

중요한 추출 규칙:
- amount: 총 결제 금액만 숫자로 (쉼표, 원화 기호 제거, 가장 큰 금액 우선)
- rawDateTime: 영수증에서 찾은 날짜/시간 텍스트 그대로 (예: "25.1.2.19:11:30", "2025년 1월 2일 19시 11분", "01/02 19:11" 등)
  * 날짜/시간을 찾을 수 없으면 빈 문자열 ""
  * 여러 날짜가 있으면 가장 최근/명확한 것 선택
- usageLocation: 상점명이나 사용처를 정확히 (브랜드명 우선, 지점명 제외)
- JSON 형식만 응답하고 다른 설명 추가 금지

추출 가이드:
1. 금액: "총 금액", "합계", "결제 금액" 등 명시된 총액 우선
2. 날짜: "거래일시", "결제일시", "영수증일시" 등 명시된 시간 우선
3. 상점: "상호명", "매장명", "브랜드명" 등 명시된 이름 우선

날짜/시간 추출 예시:
- "25.1.2.19:11:30" → rawDateTime: "25.1.2.19:11:30"
- "2025년 1월 2일 19시 11분 30초" → rawDateTime: "2025년 1월 2일 19시 11분 30초"
- "2025/01/02 19:11" → rawDateTime: "2025/01/02 19:11"
- "19:11:30" → rawDateTime: "19:11:30"
- 날짜/시간 없음 → rawDateTime: ""
"""
            
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            
            result = json.loads(response.choices[0].message.content)
            logging.info(f"LLM 원본 데이터 추출 완료: {result}")
            
            # 날짜/시간 정형화 적용
            raw_datetime = result.get('rawDateTime', '')
            normalized_datetime = self.normalize_datetime(raw_datetime)
            result['usageDateTime'] = normalized_datetime
            
            # 데이터 검증 및 보정
            amount = result.get('amount', 0)
            if isinstance(amount, str):
                # 문자열인 경우 숫자만 추출
                amount = re.sub(r'[^\d]', '', amount)
                result['amount'] = int(amount) if amount else 0
            
            usage_location = result.get('usageLocation', '')
            if not usage_location or len(usage_location.strip()) == 0:
                result['usageLocation'] = '미확인'
            
            # rawDateTime은 디버깅용으로 유지, 최종 응답에서는 제외
            logging.info(f"날짜 정형화 완료: '{raw_datetime}' → '{normalized_datetime}'")
            logging.info(f"데이터 검증 완료: 금액={result['amount']}, 사용처={result['usageLocation']}")
            
            # 캐시 저장
            self.set_redis_cache(cache_key, result, 3600)  # 1시간
            return result
            
        except Exception as e:
            logging.error(f"LLM 데이터 추출 실패: {e}")
            return {"error": str(e)}
    
    def get_db_patterns(self, usage_location: str) -> List[Dict]:
        """구매처 기반으로 과거 패턴 조회"""
        cache_key = f"receipt:pattern:{hashlib.md5(usage_location.encode()).hexdigest()}"
        
        # Redis 캐시 확인
        cached_patterns = self.get_redis_cache(cache_key)
        if cached_patterns:
            logging.info(f"✅ DB 패턴 캐시 적중: {len(cached_patterns)} 패턴")
            return cached_patterns
        
        try:
            conn = mysql.connector.connect(
                host=self.config.DB_HOST,
                port=self.config.DB_PORT,
                user=self.config.DB_USER,
                password=self.config.DB_PASSWORD,
                database=self.config.DB_NAME
            )
            cursor = conn.cursor(dictionary=True)
            
            # usageLocation 필드에서 구매처 기반 패턴 조회 (정확도 향상)
            query = """
            SELECT accountCategory, description, COUNT(*) as frequency,
                   CASE 
                       WHEN usageLocation = %s THEN 3
                       WHEN usageLocation LIKE %s THEN 2
                       WHEN usageLocation LIKE %s THEN 1
                       ELSE 0
                   END as relevance_score
            FROM expense_items 
            WHERE (usageLocation = %s OR usageLocation LIKE %s OR usageLocation LIKE %s)
              AND accountCategory IS NOT NULL
            GROUP BY accountCategory, description
            ORDER BY relevance_score DESC, frequency DESC
            LIMIT 10
            """
            
            exact_match = usage_location
            starts_with = f"{usage_location}%"
            contains = f"%{usage_location}%"
            
            cursor.execute(query, (exact_match, starts_with, contains, exact_match, starts_with, contains))
            patterns = cursor.fetchall()
            
            cursor.close()
            conn.close()
            
            # Redis에 캐싱 (30분)
            self.set_redis_cache(cache_key, patterns, 1800)
            
            logging.info(f"📊 DB에서 '{usage_location}' 패턴 {len(patterns)}개 조회")
            return patterns
            
        except Exception as e:
            logging.error(f"❌ DB 패턴 조회 실패: {e}")
            return []
    
    def read_account_category_guide(self) -> str:
        """계정과목 가이드 문서 읽기"""
        try:
            with open('account_category_list.md', 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logging.error(f"계정과목 가이드 읽기 실패: {e}")
            return ""
    
    def format_db_patterns(self, patterns: List[Dict]) -> str:
        """DB 패턴 리스트를 문자열로 변환"""
        if not patterns:
            return "해당 사용처에 대한 과거 기록이 없습니다."
        
        formatted = []
        total_frequency = sum(p['frequency'] for p in patterns)
        
        for i, pattern in enumerate(patterns[:5]):  # 상위 5개만
            percentage = (pattern['frequency'] / total_frequency * 100) if total_frequency > 0 else 0
            relevance = "정확" if pattern.get('relevance_score', 0) >= 2 else "유사"
            formatted.append(f"{i+1}. {pattern['accountCategory']}: {pattern['description']} (사용횟수: {pattern['frequency']}, 비율: {percentage:.1f}%, 매칭: {relevance})")
        
        return "\n".join(formatted)
    
    def final_judgment_with_llm(self, extracted_data: Dict, db_patterns: List[Dict], guide_text: str) -> Dict:
        """LLM으로 최종 계정과목 및 지출용도 판단"""
        cache_key = f"receipt:final:{hashlib.md5(str(extracted_data).encode()).hexdigest()}"
        
        # Redis 캐시 확인
        cached_result = self.get_redis_cache(cache_key)
        if cached_result:
            logging.info("✅ 최종 판단 캐시 적중")
            return cached_result
        
        # 날짜/시간 분석
        datetime_analysis = self.analyze_datetime(extracted_data.get('usageDateTime', ''))
        
        pattern_summary = f"""
📊 과거 패턴 분석 (usageLocation: '{extracted_data.get('usageLocation', '')}'):
{self.format_db_patterns(db_patterns)}
""" if db_patterns else "📊 과거 패턴: 해당 사용처에 대한 기록이 없습니다."
        
        try:
            prompt = f"""
영수증 데이터를 분석하여 적절한 계정과목과 지출항목을 결정해주세요.

📋 추출된 데이터:
- 금액: {extracted_data.get('amount', 0):,}원
- 사용일시: {extracted_data.get('usageDateTime', '')}
- 사용처: {extracted_data.get('usageLocation', '')}

🕐 시간 분석 정보:
- 요일: {datetime_analysis['weekday']} ({datetime_analysis['day_type']})
- 시간: {datetime_analysis['hour']}시 ({datetime_analysis['time_period']})
- 근무구분: {datetime_analysis['work_context']}
- 주말여부: {'예' if datetime_analysis['is_weekend'] else '아니오'}
- 특근/야근: {'예' if datetime_analysis['is_overtime'] else '아니오'}

{pattern_summary}

📚 계정과목 가이드 (한국 기업 실무용):
{guide_text[:2000]}...

🎯 **정확한 판단 기준**:

1. **금액 기반 판단**:
   - 1,000원 미만: 간식, 커피, 음료
   - 1,000-5,000원: 점심식대, 간식
   - 5,000-20,000원: 식대, 업무용품
   - 20,000원 이상: 회식, 장비, 소프트웨어

2. **시간대별 Description 생성**:
   **평일 (월~금)**:
   - 06:00-11:00 (오전) → "조식", "오전 커피"
   - 11:00-14:00 (점심) → "점심식대", "점심 커피" 
   - 14:00-18:00 (오후) → "오후 커피", "업무 간식"
   - 18:00-22:00 (야근) → "야근식대", "야근 커피", "야근 배달"
   - 22:00-06:00 (심야) → "심야 야근식대"

   **토요일**: 모든 시간대 → "토요 특근 식대", "토요 특근 커피"
   **일요일**: 모든 시간대 → "일요 특근 식대", "일요 특근 커피"

3. **브랜드별 우선순위**:
   - 정확한 브랜드명 매칭 우선
   - 유사한 브랜드명 차순위
   - 일반적인 카테고리 분류 최후

다음 JSON 형식으로만 응답해주세요:
{{
    "amount": {extracted_data.get('amount', 0)},
    "usageDateTime": "{extracted_data.get('usageDateTime', '')}",
    "usageLocation": "{extracted_data.get('usageLocation', '')}",
    "accountCategory": "최종_결정된_계정과목",
    "description": "시간분석_기반_구체적_지출항목",
    "reasoning": {{
        "step1_brand_analysis": "한국 브랜드 식별 및 분석 결과",
        "step2_time_analysis": "정확한 시간대 분석 - {datetime_analysis['weekday']} {datetime_analysis['hour']}시 ({datetime_analysis['work_context']})",
        "step3_db_patterns": "DB 패턴 분석 결과 및 활용도",
        "step4_guide_matching": "가이드 문서 키워드 매칭 결과",
        "step5_final_decision": "최종 판단 근거 (금액+시간+브랜드 종합 분석)",
        "confidence_level": "높음/보통/낮음"
    }}
}}

중요:
- description은 시간 분석 정보({datetime_analysis['work_context']})를 정확히 반영하세요
- 추측성 정보는 절대 추가하지 마세요 (목적지, 회의 등)
- 실제 추출된 데이터와 시간 분석만 기준으로 판단하세요
- 금액과 시간대를 종합하여 최적의 분류를 결정하세요
"""
            
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            
            result = json.loads(response.choices[0].message.content)
            logging.info(f"LLM 최종 판단 완료: {result}")
            
            # 캐시 저장
            self.set_redis_cache(cache_key, result, 1800)  # 30분
            return result
            
        except Exception as e:
            logging.error(f"LLM 최종 판단 실패: {e}")
            return {"error": str(e)}
    
    def process_receipt(self, image_data: bytes) -> Dict:
        """영수증 처리 메인 프로세스"""
        start_time = datetime.now()
        cache_used = False
        
        try:
            # 1. 이미지 해시 생성
            image_hash = self.get_image_hash(image_data)
            cache_key = f"receipt:complete:{image_hash}"
            
            # 2. 전체 결과 캐시 확인
            cached_result = self.get_redis_cache(cache_key)
            if cached_result:
                cache_used = True
                processing_time = (datetime.now() - start_time).total_seconds()
                return {
                    "success": True,
                    "data": cached_result,
                    "processing_time": f"{processing_time:.2f}s",
                    "cache_used": cache_used
                }
            
            # 3. OCR 처리 (캐시 확인)
            ocr_cache_key = f"receipt:ocr:{image_hash}"
            ocr_result = self.get_redis_cache(ocr_cache_key)
            
            if not ocr_result:
                logging.info("📸 네이버 OCR 처리 중...")
                ocr_result = self.call_naver_ocr(image_data)
                if "error" not in ocr_result:
                    self.set_redis_cache(ocr_cache_key, ocr_result, 86400)  # 24시간
            else:
                logging.info("🎯 OCR 캐시 적중")
                cache_used = True
            
            if "error" in ocr_result:
                return {"success": False, "error": ocr_result["error"]}
            
            # 4. OCR 텍스트 추출
            ocr_text = self.extract_text_from_ocr(ocr_result)
            if not ocr_text:
                return {"success": False, "error": "OCR 텍스트 추출 실패"}
            
            # 5. LLM 데이터 추출 (캐시 확인)
            text_hash = hashlib.md5(ocr_text.encode()).hexdigest()
            llm_cache_key = f"receipt:llm:{text_hash}"
            extracted_data = self.get_redis_cache(llm_cache_key)
            
            if not extracted_data:
                logging.info("🤖 LLM 데이터 추출 중...")
                extracted_data = self.extract_data_with_llm(ocr_text)
                if "error" not in extracted_data:
                    self.set_redis_cache(llm_cache_key, extracted_data, 7200)  # 2시간
            else:
                logging.info("🎯 LLM 캐시 적중")
                cache_used = True
            
            if "error" in extracted_data:
                return {"success": False, "error": extracted_data["error"]}
            
            # 6. DB 패턴 조회 (캐시 확인)
            usage_location = extracted_data.get('usageLocation', '')
            pattern_cache_key = f"receipt:pattern:{hashlib.md5(usage_location.encode()).hexdigest()}"
            db_patterns = self.get_redis_cache(pattern_cache_key)
            
            if not db_patterns:
                logging.info("🗄️ DB 패턴 조회 중...")
                db_patterns = self.get_db_patterns(usage_location)
                self.set_redis_cache(pattern_cache_key, db_patterns, 1800)  # 30분
            else:
                logging.info("🎯 패턴 캐시 적중")
                cache_used = True
            
            # 7. 계정과목 가이드 읽기
            guide_text = self.read_account_category_guide()
            
            # 8. LLM 최종 판단
            logging.info("🧠 LLM 최종 판단 중...")
            final_result = self.final_judgment_with_llm(extracted_data, db_patterns, guide_text)
            
            if "error" in final_result:
                return {"success": False, "error": final_result["error"]}
            
            # 9. 전체 결과 캐시 저장
            self.set_redis_cache(cache_key, final_result, 3600)  # 1시간
            
            processing_time = (datetime.now() - start_time).total_seconds()
            
            # 최종 결과 반환
            result = {
                "success": True,
                "data": {
                    "amount": final_result.get("amount"),
                    "usageDateTime": final_result.get("usageDateTime"), 
                    "usageLocation": final_result.get("usageLocation"),
                    "accountCategory": final_result.get("accountCategory"),
                    "description": final_result.get("description")
                },
                "reasoning": final_result.get("reasoning", {}),
                "processing_time": f"{processing_time:.2f}s",
                "cache_used": cache_used
            }
            
            return result
            
        except Exception as e:
            logging.error(f"영수증 처리 실패: {e}")
            return {"success": False, "error": str(e)}

    def normalize_datetime(self, datetime_str: str) -> str:
        """다양한 날짜/시간 형식을 표준 형식(YYYY-MM-DD HH:mm:ss)으로 정형화"""
        if not datetime_str or datetime_str.strip() == "":
            return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 공백 및 특수문자 정리 (중간점 · 보존)
        cleaned = re.sub(r'[^\d\-/.:년월일시분초·]', ' ', datetime_str.strip())
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        # 다양한 패턴들
        patterns = [
            # 24.12.18·18:31:21 형태 (YY.MM.DD·HH:mm:ss) - 중간점 포함
            (r'(\d{2})\.(\d{1,2})\.(\d{1,2})[·\s]+(\d{1,2}):(\d{2}):(\d{2})', 
             lambda m: f"20{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} {m.group(4):0>2}:{m.group(5)}:{m.group(6)}"),
            
            # 25.1.2.19:11:30 형태 (YY.M.D.HH:mm:ss) - 점으로 구분
            (r'(\d{2})\.(\d{1,2})\.(\d{1,2})\.(\d{1,2}):(\d{2}):(\d{2})', 
             lambda m: f"20{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} {m.group(4):0>2}:{m.group(5)}:{m.group(6)}"),
            
            # 2025.1.2 19:11:30 형태 (YYYY.M.D HH:mm:ss)
            (r'(\d{4})\.(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})', 
             lambda m: f"{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} {m.group(4):0>2}:{m.group(5)}:{m.group(6)}"),
            
            # 2025/01/02 19:11:30 형태 (YYYY/MM/DD HH:mm:ss)
            (r'(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})', 
             lambda m: f"{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} {m.group(4):0>2}:{m.group(5)}:{m.group(6)}"),
            
            # 25-01-02 19:11 형태 (YY-MM-DD HH:mm)
            (r'(\d{2})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', 
             lambda m: f"20{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} {m.group(4):0>2}:{m.group(5)}:00"),
            
            # 2025년 1월 2일 19시 11분 30초 형태
            (r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(\d{1,2})시\s*(\d{1,2})분\s*(\d{1,2})초', 
             lambda m: f"{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} {m.group(4):0>2}:{m.group(5)}:{m.group(6)}"),
            
            # 2025년 1월 2일 19시 11분 형태
            (r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(\d{1,2})시\s*(\d{1,2})분', 
             lambda m: f"{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} {m.group(4):0>2}:{m.group(5)}:00"),
            
            # 01/02 19:11 형태 (MM/DD HH:mm) - 올해로 가정
            (r'(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})', 
             lambda m: f"{datetime.now().year}-{m.group(1):0>2}-{m.group(2):0>2} {m.group(3):0>2}:{m.group(4)}:00"),
            
            # 19:11:30 형태 (HH:mm:ss) - 오늘 날짜로 가정
            (r'^(\d{1,2}):(\d{2}):(\d{2})$', 
             lambda m: f"{datetime.now().strftime('%Y-%m-%d')} {m.group(1):0>2}:{m.group(2)}:{m.group(3)}"),
            
            # 19:11 형태 (HH:mm) - 오늘 날짜로 가정
            (r'^(\d{1,2}):(\d{2})$', 
             lambda m: f"{datetime.now().strftime('%Y-%m-%d')} {m.group(1):0>2}:{m.group(2)}:00"),
            
            # 2025-01-02 형태 (YYYY-MM-DD) - 기본 시간 12:00:00
            (r'^(\d{4})-(\d{1,2})-(\d{1,2})$', 
             lambda m: f"{m.group(1)}-{m.group(2):0>2}-{m.group(3):0>2} 12:00:00"),
        ]
        
        # 패턴 매칭 시도
        for pattern, formatter in patterns:
            match = re.search(pattern, cleaned)
            if match:
                try:
                    result = formatter(match)
                    # 유효한 날짜인지 검증
                    datetime.strptime(result, '%Y-%m-%d %H:%M:%S')
                    logging.info(f"날짜 정형화 성공: '{datetime_str}' → '{result}'")
                    return result
                except ValueError:
                    continue
        
        # 모든 패턴이 실패하면 현재 시간 반환
        fallback = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logging.warning(f"날짜 정형화 실패, 현재 시간 사용: '{datetime_str}' → '{fallback}'")
        return fallback
    
    def analyze_datetime(self, datetime_str: str) -> dict:
        """날짜/시간을 분석하여 요일, 시간대, 근무 유형 등을 반환"""
        try:
            dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
            
            # 요일 분석 (0=월요일, 6=일요일)
            weekday = dt.weekday()
            weekday_names = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
            
            # 시간대 분석
            hour = dt.hour
            if 6 <= hour < 11:
                time_period = "오전"
                work_type = "조식/오전 업무"
            elif 11 <= hour < 14:
                time_period = "점심"
                work_type = "점심식대"
            elif 14 <= hour < 18:
                time_period = "오후"
                work_type = "오후 업무"
            elif 18 <= hour < 22:
                time_period = "야근"
                work_type = "야근식대"
            else:  # 22시 이후 또는 6시 이전
                time_period = "심야"
                work_type = "심야 야근식대"
            
            # 근무일 구분
            if weekday < 5:  # 평일 (월~금)
                day_type = "평일"
                if time_period in ["야근", "심야"]:
                    work_context = f"평일 {work_type}"
                else:
                    work_context = f"평일 {work_type}"
            elif weekday == 5:  # 토요일
                day_type = "토요일"
                work_context = f"토요 특근 {work_type.replace('식대', '식대')}"
            else:  # 일요일
                day_type = "일요일"
                work_context = f"일요 특근 {work_type.replace('식대', '식대')}"
            
            return {
                "weekday": weekday_names[weekday],
                "day_type": day_type,
                "hour": hour,
                "time_period": time_period,
                "work_type": work_type,
                "work_context": work_context,
                "is_weekend": weekday >= 5,
                "is_overtime": time_period in ["야근", "심야"] or weekday >= 5
            }
        
        except Exception as e:
            logging.error(f"날짜/시간 분석 실패: {e}")
            return {
                "weekday": "알 수 없음",
                "day_type": "알 수 없음", 
                "hour": 12,
                "time_period": "점심",
                "work_type": "점심식대",
                "work_context": "기본 식대",
                "is_weekend": False,
                "is_overtime": False
            }

# 전역 프로세서 인스턴스
processor = ReceiptProcessor()

@api.route('/process')
class ReceiptProcess(Resource):
    @api.expect(upload_parser)
    @api.marshal_with(response_model) # type: ignore
    def post(self):
        """
        🧾 **영수증 자동 처리**
        
        영수증 이미지를 업로드하면 OCR → LLM → DB 패턴 분석을 통해
        완성된 경비 데이터(금액, 일자, 계정과목, 지출항목, 사용처)를 반환합니다.
        
        **처리 과정:**
        1. 📸 네이버 CLOVA OCR로 텍스트 추출
        2. 🤖 OpenAI LLM으로 구조화된 데이터 추출
        3. 🗄️ DB에서 동일 사용처 패턴 조회
        4. 📚 계정과목 가이드 문서 참조
        5. 🧠 LLM으로 최종 계정과목 및 지출항목 결정
        
        **캐싱:** 
        - OCR 결과: 24시간
        - LLM 추출: 2시간  
        - DB 패턴: 30분
        - 전체 결과: 1시간
        """
        try:
            # 파일 업로드 확인
            if 'image' not in request.files:
                return {"success": False, "error": "이미지 파일이 필요합니다"}, 400
            
            file = request.files['image']
            if file.filename == '':
                return {"success": False, "error": "파일이 선택되지 않았습니다"}, 400
            
            # 이미지 파일 검증
            if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                return {"success": False, "error": "PNG, JPG, JPEG 파일만 지원됩니다"}, 400
            
            # 이미지 데이터 읽기
            image_data = file.read()
            if len(image_data) == 0:
                return {"success": False, "error": "빈 파일입니다"}, 400
            
            # 영수증 처리
            result = processor.process_receipt(image_data)
            
            if result["success"]:
                return result, 200
            else:
                return result, 500
                
        except Exception as e:
            logging.error(f"API 처리 실패: {e}")
            return {"success": False, "error": str(e)}, 500

@api.route('/health')
class Health(Resource):
    def get(self):
        """🔍 시스템 상태 확인"""
        try:
            status = {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "services": {
                    "redis": redis_client is not None,
                    "openai": openai_client is not None,
                    "clova_ocr": Config.CLOVA_OCR_API_KEY is not None,
                    "database": all([
                        Config.DB_HOST, Config.DB_USER, 
                        Config.DB_PASSWORD, Config.DB_NAME
                    ])
                }
            }
            
            # Redis 연결 테스트
            if redis_client:
                try:
                    redis_client.ping()
                    status["services"]["redis_ping"] = True
                except:
                    status["services"]["redis_ping"] = False
            
            return status, 200
            
        except Exception as e:
            return {"status": "error", "error": str(e)}, 500

if __name__ == '__main__':
    logging.info("🚀 Simple Receipt Processor 시작")
    logging.info(f"📊 Redis 상태: {'연결됨' if redis_client else '비활성화'}")
    logging.info(f"🤖 OpenAI 상태: {'연결됨' if openai_client else '비활성화'}")
    logging.info(f"📸 CLOVA OCR 상태: {'설정됨' if Config.CLOVA_OCR_API_KEY else '미설정'}")
    
    app.run(
        host='0.0.0.0',
        port=5001,
        debug=True
    ) 