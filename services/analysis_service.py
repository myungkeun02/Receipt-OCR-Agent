import logging
from typing import Dict, List, Optional, Tuple
from utils.data_parser import extract_keywords_from_ocr, get_price_range
from services.db_service import db_service
from services.llm_service import llm_service
from config.settings import Config

class AnalysisService:
    """패턴 분석 및 스마트 추천을 담당하는 서비스 클래스"""
    
    def __init__(self):
        self.config = Config
        self.confidence_threshold = self.config.CONFIDENCE_THRESHOLD
    
    def analyze_and_suggest(self, 
                          ocr_data: str, 
                          amount: int, 
                          usage_date: str) -> Dict:
        """
        OCR 데이터를 분석하여 계정과목과 설명 추천
        
        Args:
            ocr_data: OCR로 추출한 사용처
            amount: 금액
            usage_date: 사용일
            
        Returns:
            Dict: 분석 결과 및 추천 정보
        """
        logging.info(f"Starting analysis for: {ocr_data}, amount: {amount}")
        
        # 1. 기본 데이터 분석
        keywords = extract_keywords_from_ocr(ocr_data)
        price_range = get_price_range(amount)
        
        logging.info(f"Extracted keywords: {keywords}, price_range: {price_range}")
        
        # 2. 히스토리 패턴 분석
        historical_result = self._analyze_historical_patterns(ocr_data, keywords, price_range)
        
        # 3. 계정과목 목록 조회
        account_categories = db_service.get_account_categories()
        
        # 4. 최종 추천 결정
        final_suggestion = self._make_final_suggestion(
            ocr_data, amount, usage_date, 
            account_categories, historical_result
        )
        
        # 5. 결과 구성
        return self._build_analysis_result(
            keywords, price_range, historical_result, 
            final_suggestion, account_categories
        )
    
    def _analyze_historical_patterns(self, 
                                   ocr_data: str, 
                                   keywords: List[str], 
                                   price_range: str) -> Dict:
        """히스토리 패턴 분석"""
        
        # 1. 정확한 매칭 시도
        exact_match = db_service.find_exact_match(ocr_data)
        if exact_match:
            account, description, confidence = exact_match
            return {
                'method': 'exact_match',
                'account_category': account,
                'description': description,
                'confidence': confidence,
                'source': 'database'
            }
        
        # 2. 키워드 매칭 시도
        keyword_match = db_service.find_keyword_matches(keywords, price_range)
        if keyword_match:
            account, description, confidence = keyword_match
            return {
                'method': 'keyword_match',
                'account_category': account,
                'description': description,
                'confidence': confidence,
                'source': 'database'
            }
        
        # 3. 가격 패턴 분석
        price_pattern = db_service.get_price_pattern_analysis(keywords, price_range)
        if price_pattern:
            account, description, confidence = price_pattern
            return {
                'method': 'price_pattern',
                'account_category': account,
                'description': description,
                'confidence': confidence,
                'source': 'database'
            }
        
        # 4. 패턴을 찾지 못한 경우
        return {
            'method': 'no_pattern',
            'account_category': None,
            'description': None,
            'confidence': 0,
            'source': 'none'
        }
    
    def _make_final_suggestion(self, 
                             ocr_data: str, 
                             amount: int, 
                             usage_date: str,
                             account_categories: List[Dict],
                             historical_result: Dict) -> Dict:
        """최종 추천 결정"""
        
        confidence = historical_result.get('confidence', 0)
        
        if confidence >= self.confidence_threshold:
            # 높은 신뢰도 -> DB 결과 사용
            logging.info(f"Using high-confidence DB suggestion (confidence: {confidence})")
            
            return {
                'account_category': historical_result['account_category'],
                'description': historical_result['description'],
                'confidence': confidence,
                'source': 'historical_data',
                'method': historical_result['method']
            }
        
        else:
            # 낮은 신뢰도 -> LLM 호출
            logging.info(f"Using LLM suggestion (DB confidence too low: {confidence})")
            
            # LLM에게 히스토리 힌트 제공
            historical_hint = None
            if historical_result['account_category']:
                historical_hint = (
                    historical_result['account_category'], 
                    historical_result['description']
                )
            
            account, description = llm_service.suggest_account_category(
                ocr_data, amount, usage_date, account_categories, historical_hint
            )
            
            # LLM 신뢰도 계산
            llm_confidence = self._calculate_llm_confidence(historical_hint, confidence)
            
            return {
                'account_category': account,
                'description': description,
                'confidence': llm_confidence,
                'source': 'llm_with_history' if historical_hint else 'llm_only',
                'method': 'llm_analysis'
            }
    
    def _calculate_llm_confidence(self, historical_hint: Optional[Tuple], db_confidence: int) -> int:
        """LLM 추천의 신뢰도 계산"""
        base_confidence = 50  # LLM 기본 신뢰도
        
        if historical_hint:
            # DB 힌트가 있으면 신뢰도 증가
            bonus = min(20, db_confidence // 4)  # 최대 20점 추가
            return base_confidence + bonus
        
        return base_confidence
    
    def _build_analysis_result(self, 
                             keywords: List[str],
                             price_range: str,
                             historical_result: Dict,
                             final_suggestion: Dict,
                             account_categories: List[Dict]) -> Dict:
        """분석 결과 구성"""
        
        return {
            'analysis_info': {
                'keywords': keywords,
                'price_range': price_range,
                'historical_method': historical_result.get('method'),
                'historical_confidence': historical_result.get('confidence', 0)
            },
            'suggestion': {
                'account_category': final_suggestion['account_category'],
                'description': final_suggestion['description'],
                'confidence': final_suggestion['confidence'],
                'source': final_suggestion['source'],
                'method': final_suggestion['method']
            },
            'available_categories': [cat['name'] for cat in account_categories],
            'performance_metrics': {
                'used_database': final_suggestion['source'] in ['historical_data'],
                'used_llm': final_suggestion['source'] in ['llm_only', 'llm_with_history'],
                'confidence_level': self._get_confidence_level(final_suggestion['confidence'])
            }
        }
    
    def _get_confidence_level(self, confidence: int) -> str:
        """신뢰도 레벨 분류"""
        if confidence >= 90:
            return "very_high"
        elif confidence >= 80:
            return "high"
        elif confidence >= 60:
            return "medium"
        elif confidence >= 40:
            return "low"
        else:
            return "very_low"
    
    def get_statistics(self) -> Dict:
        """분석 서비스 통계 정보"""
        db_stats = db_service.get_statistics()
        llm_info = llm_service.get_model_info()
        
        return {
            'database_stats': db_stats,
            'llm_info': llm_info,
            'analysis_config': {
                'confidence_threshold': self.confidence_threshold,
                'price_ranges': ['very_low', 'low', 'medium', 'high', 'very_high']
            }
        }
    
    def validate_suggestion(self, suggestion: Dict) -> bool:
        """추천 결과 검증"""
        required_fields = ['account_category', 'description', 'confidence', 'source']
        
        if not all(field in suggestion for field in required_fields):
            logging.error(f"Missing required fields in suggestion: {suggestion}")
            return False
        
        if suggestion['confidence'] < 0 or suggestion['confidence'] > 100:
            logging.error(f"Invalid confidence value: {suggestion['confidence']}")
            return False
        
        if not suggestion['account_category'] or not suggestion['description']:
            logging.error(f"Empty account_category or description in suggestion")
            return False
        
        return True
    
    def learn_from_feedback(self, 
                          ocr_data: str, 
                          amount: int, 
                          usage_date: str,
                          original_suggestion: str,
                          corrected_category: str,
                          corrected_description: str) -> bool:
        """사용자 피드백으로부터 학습"""
        
        try:
            # expense_items에 수정된 데이터 저장
            expense_data = {
                'ocrData': ocr_data,
                'amount': amount,
                'usageDate': usage_date,
                'accountCategory': corrected_category,
                'description': corrected_description
            }
            
            success = db_service.save_expense_item(expense_data)
            
            if success:
                logging.info(f"Learning feedback saved: {ocr_data} -> {corrected_category}")
                return True
            else:
                logging.error(f"Failed to save learning feedback for: {ocr_data}")
                return False
                
        except Exception as e:
            logging.error(f"Error in learn_from_feedback: {e}")
            return False

# 싱글톤 인스턴스 생성
analysis_service = AnalysisService() 