import json
import logging
from typing import Dict, List, Optional, Tuple
from openai import OpenAI
from config.settings import Config

class LLMService:
    """OpenAI LLM API를 담당하는 서비스 클래스"""
    
    def __init__(self):
        self.config = Config
        self.api_key = self.config.LLM_API_KEY
        self.model = self.config.LLM_MODEL
        self.temperature = self.config.LLM_TEMPERATURE
        self.client = None
        
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
    
    def extract_structured_data(self, raw_text: str) -> Dict:
        """
        OCR 텍스트에서 구조화된 데이터 추출 (Redis 캐싱 적용)
        
        Args:
            raw_text: OCR로 추출한 원본 텍스트
            
        Returns:
            Dict: 구조화된 영수증 데이터
        """
        # Redis 캐싱 적용
        try:
            from services.cache_service import redis_cache_manager
            import hashlib
            
            # 텍스트 해시 생성 (프롬프트 기반)
            prompt = self._build_extraction_prompt(raw_text)
            prompt_hash = hashlib.md5(prompt.encode()).hexdigest()
            
            # 캐시에서 먼저 확인
            cached_result = redis_cache_manager.get_cached_llm_response(prompt_hash)
            if cached_result:
                logging.info(f"🎯 LLM cache hit - using cached result for {prompt_hash[:12]}...")
                return cached_result
        except Exception as cache_error:
            logging.warning(f"LLM cache check failed: {cache_error}")
        
        if not self.client:
            logging.warning("LLM API key not set. Using mock extraction.")
            mock_result = self._get_mock_structured_data()
            
            # Mock 결과도 캐싱 (30분)
            try:
                redis_cache_manager.cache_llm_response(prompt_hash, mock_result, expire_minutes=30)
            except:
                pass
            
            return mock_result
        
        try:
            logging.info(f"🤖 Processing new text with LLM: {prompt_hash[:12]}...")
            
            logging.info("Sending data extraction request to OpenAI")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=self.temperature
            )
            
            result_text = response.choices[0].message.content
            
            if not result_text:
                raise ValueError("Empty response from LLM")
            
            logging.info(f"Received LLM extraction response: {result_text}")
            extracted_data = json.loads(result_text)
            
            # 데이터 검증 및 정리
            validated_data = self._validate_and_clean_extracted_data(extracted_data)
            
            # 성공한 결과를 Redis에 캐싱 (2시간)
            try:
                redis_cache_manager.cache_llm_response(prompt_hash, validated_data, expire_minutes=120)
                logging.info(f"✅ LLM result cached for {prompt_hash[:12]}...")
            except Exception as cache_error:
                logging.warning(f"LLM result caching failed: {cache_error}")
            
            return validated_data
            
        except Exception as e:
            logging.error(f"LLM data extraction failed: {e}")
            fallback_result = {"error": "LLM data extraction failed"}
            
            # 실패 시에도 짧은 시간 캐싱 (재시도 방지)
            try:
                redis_cache_manager.cache_llm_response(prompt_hash, fallback_result, expire_minutes=10)
            except:
                pass
            
            return fallback_result
    
    def suggest_account_category(self, 
                               ocr_data: str, 
                               amount: int, 
                               usage_date: str, 
                               account_categories: List[Dict],
                               historical_suggestion: Optional[Tuple[str, str]] = None) -> Tuple[str, str]:
        """
        계정과목과 설명 추천
        
        Args:
            ocr_data: OCR 추출 데이터 (사용처)
            amount: 금액
            usage_date: 사용일
            account_categories: 사용 가능한 계정과목 목록
            historical_suggestion: DB에서 찾은 히스토리 제안
            
        Returns:
            Tuple[account_category, description]: 추천된 계정과목과 설명
        """
        if not self.client:
            logging.warning("LLM API key not set. Using fallback suggestion.")
            return self._get_fallback_suggestion(ocr_data, historical_suggestion)
        
        try:
            prompt = self._build_categorization_prompt(
                ocr_data, amount, usage_date, account_categories, historical_suggestion
            )
            
            logging.info("Sending categorization request to OpenAI")
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=self.temperature
            )
            
            result_text = response.choices[0].message.content
            
            if not result_text:
                raise ValueError("Empty response from LLM")
            
            result = json.loads(result_text)
            account_category = result.get('account_category', '소모품비')
            description = result.get('description', '기타')
            
            logging.info(f"LLM suggested: {account_category} / {description}")
            return account_category, description
            
        except Exception as e:
            logging.error(f"LLM categorization failed: {e}")
            return self._get_fallback_suggestion(ocr_data, historical_suggestion)
    
    def _build_extraction_prompt(self, raw_text: str) -> str:
        """데이터 추출용 프롬프트 생성"""
        return f"""다음은 한국어 영수증에서 OCR로 추출한 텍스트입니다. 
이 텍스트를 분석하여 아래 정보를 JSON 형식으로 추출해주세요.

**원본 텍스트:**
{raw_text}

**추출할 정보:**
- merchant_name: 상호명/사용처 (예: "스타벅스 강남점")
- transaction_date: 거래일 (YYYY-MM-DD 형식)
- total_price: 총 금액 (정수, 단위: 원)
- approval_no: 승인번호 (카드 결제시)

**응답 형식:**
{{
    "merchant_name": "추출된 상호명",
    "transaction_date": "2025-07-09",
    "total_price": 15000,
    "approval_no": "승인번호 또는 null"
}}

**주의사항:**
- 정확히 식별할 수 없는 필드는 null로 설정
- 금액은 숫자만 (콤마, 원 단위 제거)
- 날짜는 반드시 YYYY-MM-DD 형식
- JSON 형식만 응답해주세요."""

    def _build_categorization_prompt(self, 
                                   ocr_data: str, 
                                   amount: int, 
                                   usage_date: str, 
                                   account_categories: List[Dict],
                                   historical_suggestion: Optional[Tuple[str, str]]) -> str:
        """계정과목 분류용 프롬프트 생성"""
        
        # 계정과목 목록을 문자열로 변환
        categories_text = "\\n".join([
            f"- {cat['name']} ({cat['code']}): {cat['description']}" 
            for cat in account_categories
        ])
        
        # 히스토리 컨텍스트 추가
        history_context = ""
        if historical_suggestion and historical_suggestion[0]:
            history_context = f"""

**참고 정보 (과거 유사 거래):**
- 계정과목: {historical_suggestion[0]}
- 사용 용도: {historical_suggestion[1]}
이 정보를 참고하되, 현재 거래에 더 적합한 분류가 있다면 그것을 우선해주세요."""
        
        return f"""다음 영수증 정보를 분석하여 가장 적절한 회계 계정과목과 사용 용도를 추천해주세요.

**거래 정보:**
- 사용처: {ocr_data}
- 금액: {amount:,}원
- 사용일: {usage_date}
{history_context}

**사용 가능한 계정과목 및 해당 항목들:**
{categories_text}

**응답 형식 (JSON):**
{{
    "account_category": "추천 계정과목명",
    "description": "구체적인 사용 용도 (예: 야근 식대, 숙박비, 커피)",
    "reasoning": "선택 이유"
}}

**중요한 구분:**
- account_category: 회계상 분류 (예: 복리후생비, 여비교통비)
- description: 실제 사용 목적/용도 (예: 야근 식대, 숙박비, 커피, 주유비)

**분류 가이드라인:**
- 사용처와 위 계정과목 설명을 매칭하여 적절한 계정과목 선택
- description은 실제로 무엇에 사용했는지 구체적으로 작성
- 금액과 사용처를 종합 고려
- JSON 형식으로만 응답해주세요."""

    def _validate_and_clean_extracted_data(self, data: Dict) -> Dict:
        """추출된 데이터 검증 및 정리"""
        cleaned_data = {}
        
        # merchant_name 정리
        merchant_name = data.get('merchant_name')
        if merchant_name and isinstance(merchant_name, str):
            cleaned_data['merchant_name'] = merchant_name.strip()[:200]  # 길이 제한
        else:
            cleaned_data['merchant_name'] = None
        
        # transaction_date 검증
        transaction_date = data.get('transaction_date')
        if transaction_date and self._is_valid_date_format(transaction_date):
            cleaned_data['transaction_date'] = transaction_date
        else:
            cleaned_data['transaction_date'] = None
        
        # total_price 정리
        total_price = data.get('total_price')
        if isinstance(total_price, (int, float)) and total_price >= 0:
            cleaned_data['total_price'] = int(total_price)
        else:
            cleaned_data['total_price'] = None
        
        # approval_no 정리
        approval_no = data.get('approval_no')
        if approval_no and isinstance(approval_no, str):
            cleaned_data['approval_no'] = approval_no.strip()[:50]
        else:
            cleaned_data['approval_no'] = None
        
        return cleaned_data
    
    def _is_valid_date_format(self, date_str: str) -> bool:
        """날짜 형식 검증 (YYYY-MM-DD)"""
        try:
            from datetime import datetime
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except (ValueError, TypeError):
            return False
    
    def _get_mock_structured_data(self) -> Dict:
        """API 키가 없을 때 사용할 목업 데이터"""
        return {
            "merchant_name": "목업 상점",
            "transaction_date": "2025-07-11",
            "total_price": 12345,
            "approval_no": "987654"
        }
    
    def _get_fallback_suggestion(self, 
                               ocr_data: str, 
                               historical_suggestion: Optional[Tuple[str, str]]) -> Tuple[str, str]:
        """LLM 사용 불가시 폴백 추천"""
        if historical_suggestion:
            return historical_suggestion
        
        # 키워드 기반 간단한 분류 (계정과목, 실제 사용 용도)
        ocr_lower = ocr_data.lower()
        
        if any(keyword in ocr_lower for keyword in ['스타벅스', '카페', '커피']):
            return "복리후생비", "커피"
        elif any(keyword in ocr_lower for keyword in ['주유소', '기름', '셀프']):
            return "차량유지비", "주유비"
        elif any(keyword in ocr_lower for keyword in ['마트', '슈퍼', '편의점']):
            return "소모품비", "생필품 구매"
        elif any(keyword in ocr_lower for keyword in ['택시', '버스', '지하철']):
            return "여비교통비", "교통비"
        elif any(keyword in ocr_lower for keyword in ['숙박', '호텔', '모텔']):
            return "여비교통비", "숙박비"
        elif any(keyword in ocr_lower for keyword in ['병원', '약국', '의원']):
            return "복리후생비", "의료비"
        else:
            return "소모품비", "기타 구매"
    
    def get_model_info(self) -> Dict:
        """현재 설정된 LLM 모델 정보 반환"""
        return {
            "model": self.model,
            "temperature": self.temperature,
            "api_available": bool(self.client),
            "api_key_configured": bool(self.api_key)
        }

# 싱글톤 인스턴스 생성
llm_service = LLMService() 