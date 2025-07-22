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
from werkzeug.datastructures import FileStorage
from openai import OpenAI
from dotenv import load_dotenv

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
    'data': fields.Raw(description='추출된 영수증 데이터'),
    'reasoning': fields.Raw(description='AI 판단 과정 및 추론 근거'),
    'processing_time': fields.String(description='처리 시간'),
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
        """LLM으로 구조화된 데이터 추출"""
        if not openai_client:
            return {"error": "OpenAI API 키가 설정되지 않았습니다"}
        
        try:
            prompt = f"""
다음 영수증 OCR 텍스트에서 정확한 정보를 추출해주세요.

OCR 텍스트:
{ocr_text}

다음 JSON 형식으로만 응답해주세요:
{{
    "amount": 숫자형태의_총금액,
    "usageDate": "YYYY-MM-DD",
    "usageLocation": "상점명_또는_사용처"
}}

주의사항:
- amount는 총 결제 금액만 숫자로 (쉼표 제거)
- usageDate는 YYYY-MM-DD 형식 (오늘 날짜: {datetime.now().strftime('%Y-%m-%d')})
- usageLocation은 상점명이나 사용처를 정확히
- 날짜가 불명확하면 오늘 날짜 사용
- JSON 형식만 응답하고 다른 설명 추가 금지
"""
            
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            
            result = json.loads(response.choices[0].message.content)
            logging.info(f"LLM 데이터 추출 완료: {result}")
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
            
            # usageLocation 필드에서 구매처 기반 패턴 조회
            query = """
            SELECT accountCategory, description, COUNT(*) as frequency
            FROM expense_items 
            WHERE usageLocation LIKE %s AND accountCategory IS NOT NULL
            GROUP BY accountCategory, description
            ORDER BY frequency DESC
            LIMIT 10
            """
            
            search_term = f"%{usage_location}%"
            cursor.execute(query, (search_term,))
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
    
    def final_judgment_with_llm(self, extracted_data: Dict, db_patterns: List[Dict], guide_text: str) -> Dict:
        """LLM으로 최종 판단"""
        if not openai_client:
            return {"error": "OpenAI API 키가 설정되지 않았습니다"}
        
        try:
            # DB 패턴 요약
            pattern_summary = ""
            if db_patterns:
                pattern_summary = "과거 동일/유사 사용처 패턴:\n"
                for pattern in db_patterns[:5]:  # 상위 5개만
                    pattern_summary += f"- {pattern['accountCategory']}: {pattern['description']} (사용횟수: {pattern['frequency']})\n"
            else:
                pattern_summary = "과거 패턴 없음 - 신규 사용처"
            
            prompt = f"""
영수증 데이터를 분석하여 적절한 계정과목과 지출항목을 결정해주세요.

📋 추출된 데이터:
- 금액: {extracted_data.get('amount', 0):,}원
- 사용일자: {extracted_data.get('usageDate', '')}
- 사용처: {extracted_data.get('usageLocation', '')}

{pattern_summary}

📚 계정과목 가이드 (한국 기업 실무용):
{guide_text[:2000]}...

🎯 **추가 판단 기준**:
1. **한국 브랜드 우선 매칭** (400개+ 브랜드):
   - **음식**: 스타벅스, 투썸플레이스, 이디야, 빽다방, 메가커피, 컴포즈커피, 할리스, 엔젤리너스, 맥도날드, 버거킹, KFC, 롯데리아, 맘스터치, 서브웨이, 도미노피자, 피자헛, 김밥천국, BHC, 굽네치킨, 교촌치킨, 네네치킨, 배달의민족, 쿠팡이츠, 요기요
   - **이커머스**: 쿠팡, 11번가, 지마켓, 옥션, 티몬, 위메프, SSG닷컴, 롯데온, 다나와, 예스24, 교보문고, 무신사, 29CM
   - **AI/소프트웨어**: ChatGPT Plus, Claude Pro, GitHub Copilot, Notion AI, Microsoft 365, Adobe Creative Cloud, Figma, Slack, Postman
   - **교통**: SRT(에스알), KTX(케이티엑스), 카카오택시, 우버, 타다, 대한항공, 아시아나, 제주항공
   - **금융**: 국민은행(케이비), 신한은행, 하나은행, 카카오뱅크, 토스뱅크, 카카오페이, 네이버페이, 토스
   - **통신**: SK텔레콤(에스케이), KT(케이티), LG유플러스(엘지)
   - **숙박**: 야놀자, 여기어때, 롯데호텔, 신라호텔
   - **엔터**: 넷플릭스, 디즈니플러스, 멜론, 지니뮤직, CGV, 롯데시네마, 메가박스
2. **OCR 오인식 고려**: 
   - "에스알"=SRT, "케이티엑스"=KTX, "지에스"=GS, "에스케이"=SK, "케이티"=KT, "엘지"=LG, "씨유"=CU, "일일번가"=11번가, "케이비"=KB
3. **시간대별 Description 구분**: 
   - 평일 점심(11-14시): "점심식대", "점심 커피"
   - 평일 저녁(18시 이후): "야근식대", "야근 커피", "야근 배달식대"
   - 주말: "주말근무 식대", "주말근무 커피"
4. **AccountCategory vs Description 명확 구분**:
   - **accountCategory**: 회계 계정과목 (예: "복리후생비", "여비교통비", "사무용품비", "소프트웨어비")
   - **description**: 구체적 지출용도 (예: "스타벅스 회의", "SRT 출장", "쿠팡 사무용품", "ChatGPT 구독료")
5. **업무 맥락별 AccountCategory 분류**:
   - 음식/카페 → "복리후생비"
   - 교통/출장 → "여비교통비"  
   - 사무용품/장비 → "사무용품비"
   - AI/소프트웨어 → "소프트웨어비" 또는 "복리후생비"
   - 통신/인터넷 → "통신비"
   - 숙박 → "여비교통비"

다음 JSON 형식으로만 응답해주세요:
{{
    "amount": {extracted_data.get('amount', 0)},
    "usageDate": "{extracted_data.get('usageDate', '')}",
    "usageLocation": "{extracted_data.get('usageLocation', '')}",
    "accountCategory": "최종_결정된_계정과목",
    "description": "상황별_구체적_지출항목",
    "reasoning": {{
        "step1_brand_analysis": "한국 브랜드 식별 및 분석 결과 (OCR 추출 데이터 기반)",
        "step2_time_analysis": "시간대 분석 (평일/주말, 시간대별 판단)",
        "step3_db_patterns": "DB 패턴 분석 결과 및 활용도",
        "step4_guide_matching": "가이드 문서 키워드 매칭 결과", 
        "step5_final_decision": "최종 판단 근거 (추측 정보 제외, 실제 데이터만 사용)",
        "confidence_level": "높음/보통/낮음"
    }}
}}

중요: 
- JSON만 응답하고 다른 텍스트는 포함하지 마세요
- description은 시간대와 상황을 고려한 구체적 표현 사용
- 한국 기업 실무 환경에 적합한 판단 기준 적용
- **추측성 정보 금지**: OCR에서 추출되지 않은 목적지, 용도 등은 임의로 추가하지 마세요
- **실제 데이터만 사용**: 추출된 usageLocation, usageDate, amount만 기준으로 판단하세요

예시:
- ✅ "SRT 출장" (목적지 불명시)
- ❌ "SRT 부산 출장" (부산은 OCR에서 추출되지 않음)
- ✅ "카카오택시 업무이동" 
- ❌ "카카오택시 강남역 이동" (강남역은 OCR에서 추출되지 않음)
- ✅ "스타벅스 야간 커피" (시간대 기반)
- ❌ "스타벅스 회의용 커피" (회의는 OCR에서 추출되지 않음)
- ✅ "맥도날드 점심식대" (시간대 기반)
- ❌ "맥도날드 팀 점심" (팀은 OCR에서 추출되지 않음)
"""
            
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2
            )
            
            result = json.loads(response.choices[0].message.content)
            logging.info(f"LLM 최종 판단 완료: {result}")
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
                    "usageDate": final_result.get("usageDate"), 
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