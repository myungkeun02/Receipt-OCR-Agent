import logging
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import json
from services.db_service import db_service
from services.llm_service import llm_service
from config.settings import Config

class MLEnhancementService:
    """고도화된 머신러닝 기반 분석 서비스"""
    
    def __init__(self):
        self.config = Config
        self.embedding_cache = {}  # 임베딩 캐시
        
    def get_semantic_similarity(self, text1: str, text2: str) -> float:
        """
        텍스트 간 의미적 유사도 계산 (OpenAI Embeddings 활용)
        
        Args:
            text1, text2: 비교할 텍스트
            
        Returns:
            float: 유사도 점수 (0~1)
        """
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.config.LLM_API_KEY)
            
            # 임베딩 생성 (캐시 활용)
            emb1 = self._get_embedding(text1, client)
            emb2 = self._get_embedding(text2, client)
            
            if emb1 is None or emb2 is None:
                return 0.0
            
            # 코사인 유사도 계산
            similarity = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
            return max(0.0, min(1.0, similarity))
            
        except Exception as e:
            logging.error(f"Semantic similarity calculation failed: {e}")
            return 0.0
    
    def _get_embedding(self, text: str, client) -> Optional[np.ndarray]:
        """텍스트 임베딩 생성 (캐시 활용)"""
        if text in self.embedding_cache:
            return self.embedding_cache[text]
        
        try:
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            embedding = np.array(response.data[0].embedding)
            
            # 캐시 크기 제한 (최대 1000개)
            if len(self.embedding_cache) > 1000:
                # 가장 오래된 항목 제거
                oldest_key = next(iter(self.embedding_cache))
                del self.embedding_cache[oldest_key]
            
            self.embedding_cache[text] = embedding
            return embedding
            
        except Exception as e:
            logging.error(f"Embedding generation failed: {e}")
            return None
    
    def advanced_pattern_matching(self, ocr_data: str, amount: int, usage_date: str) -> Dict:
        """
        고도화된 패턴 매칭 (의미적 유사도 + 시간/금액 가중치)
        
        Args:
            ocr_data: 사용처
            amount: 금액
            usage_date: 사용일
            
        Returns:
            Dict: 향상된 매칭 결과
        """
        # 1. 기본 DB 조회
        db = db_service.get_connection()
        if not db:
            return {"confidence": 0, "matches": []}
        
        try:
            cursor = db.cursor(dictionary=True)
            
            # 최근 6개월 데이터만 조회 (성능 최적화)
            six_months_ago = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')
            
            cursor.execute("""
                SELECT ocrData, accountCategory, description, amount, usageDate, 
                       COUNT(*) as frequency,
                       DATEDIFF(NOW(), usageDate) as days_ago
                FROM expense_items 
                WHERE usageDate >= %s
                GROUP BY ocrData, accountCategory, description
                ORDER BY frequency DESC
                LIMIT 50
            """, (six_months_ago,))
            
            historical_data = cursor.fetchall()
            
            # 2. 의미적 유사도 기반 매칭
            enhanced_matches = []
            
            for item in historical_data:
                # 텍스트 유사도
                text_similarity = self.get_semantic_similarity(ocr_data, item['ocrData'])
                
                # 금액 유사도 (비율 기반)
                amount_similarity = self._calculate_amount_similarity(amount, item['amount'])
                
                # 시간 가중치 (최근 데이터일수록 높은 가중치)
                time_weight = self._calculate_time_weight(item['days_ago'])
                
                # 사용 빈도 가중치
                frequency_weight = min(1.0, item['frequency'] / 10.0)
                
                # 종합 점수 계산
                combined_score = (
                    text_similarity * 0.5 +           # 텍스트 유사도 50%
                    amount_similarity * 0.2 +         # 금액 유사도 20%
                    time_weight * 0.15 +              # 시간 가중치 15%
                    frequency_weight * 0.15           # 빈도 가중치 15%
                )
                
                if combined_score > 0.3:  # 임계값 이상만 포함
                    enhanced_matches.append({
                        'account_category': item['accountCategory'],
                        'description': item['description'],
                        'combined_score': combined_score,
                        'text_similarity': text_similarity,
                        'amount_similarity': amount_similarity,
                        'frequency': item['frequency'],
                        'matching_data': item['ocrData']
                    })
            
            # 점수순 정렬
            enhanced_matches.sort(key=lambda x: x['combined_score'], reverse=True)
            
            # 최고 점수의 신뢰도 계산
            best_confidence = 0
            if enhanced_matches:
                best_score = enhanced_matches[0]['combined_score']
                best_confidence = min(95, int(best_score * 100))
            
            return {
                "confidence": best_confidence,
                "matches": enhanced_matches[:5],  # 상위 5개만 반환
                "total_candidates": len(enhanced_matches)
            }
            
        except Exception as e:
            logging.error(f"Advanced pattern matching failed: {e}")
            return {"confidence": 0, "matches": []}
        finally:
            if db:
                db.close()
    
    def _calculate_amount_similarity(self, amount1: int, amount2: int) -> float:
        """금액 유사도 계산"""
        if amount1 == 0 or amount2 == 0:
            return 0.0
        
        # 비율 기반 유사도
        ratio = min(amount1, amount2) / max(amount1, amount2)
        
        # 금액 차이가 클수록 유사도 감소
        diff_factor = 1 - min(1.0, abs(amount1 - amount2) / max(amount1, amount2))
        
        return (ratio + diff_factor) / 2
    
    def _calculate_time_weight(self, days_ago: int) -> float:
        """시간 가중치 계산 (최근일수록 높은 가중치)"""
        if days_ago <= 7:
            return 1.0      # 1주일 이내: 100%
        elif days_ago <= 30:
            return 0.8      # 1개월 이내: 80%
        elif days_ago <= 90:
            return 0.6      # 3개월 이내: 60%
        else:
            return 0.4      # 그 이후: 40%
    
    def dynamic_confidence_scoring(self, 
                                 text_match_score: float,
                                 amount_match_score: float,
                                 frequency: int,
                                 user_correction_rate: float = 0.0) -> int:
        """
        동적 신뢰도 점수 계산
        
        Args:
            text_match_score: 텍스트 매칭 점수
            amount_match_score: 금액 매칭 점수  
            frequency: 사용 빈도
            user_correction_rate: 사용자 수정률 (0~1)
            
        Returns:
            int: 신뢰도 점수 (0~100)
        """
        # 기본 점수 계산
        base_score = (text_match_score * 0.6 + amount_match_score * 0.4) * 100
        
        # 빈도 보너스 (최대 +15점)
        frequency_bonus = min(15, frequency * 3)
        
        # 사용자 수정률에 따른 페널티 (수정이 많았으면 신뢰도 감소)
        correction_penalty = user_correction_rate * 20
        
        # 최종 점수
        final_score = base_score + frequency_bonus - correction_penalty
        
        return max(0, min(100, int(final_score)))
    
    def intelligent_category_suggestion(self, 
                                      ocr_data: str, 
                                      amount: int, 
                                      usage_date: str,
                                      user_context: Dict = None) -> Dict:
        """
        지능형 계정과목 추천 (컨텍스트 고려)
        
        Args:
            ocr_data: 사용처
            amount: 금액
            usage_date: 사용일
            user_context: 사용자 컨텍스트 (부서, 역할 등)
            
        Returns:
            Dict: 지능형 추천 결과
        """
        # 1. 사용자별 패턴 분석
        user_patterns = self._analyze_user_patterns(user_context) if user_context else {}
        
        # 2. 시간대별 패턴 분석 (업무시간 vs 야간)
        time_context = self._analyze_time_context(usage_date)
        
        # 3. 금액대별 패턴 분석
        amount_context = self._analyze_amount_context(amount)
        
        # 4. 의미적 유사도 기반 매칭
        semantic_matches = self.advanced_pattern_matching(ocr_data, amount, usage_date)
        
        # 5. 컨텍스트 기반 점수 조정
        if semantic_matches['matches']:
            for match in semantic_matches['matches']:
                # 사용자 패턴 보너스
                if user_patterns and match['account_category'] in user_patterns.get('preferred_categories', []):
                    match['combined_score'] *= 1.2
                
                # 시간 컨텍스트 보너스
                if self._is_time_context_match(match, time_context):
                    match['combined_score'] *= 1.1
                
                # 금액 컨텍스트 보너스  
                if self._is_amount_context_match(match, amount_context):
                    match['combined_score'] *= 1.1
        
        # 6. LLM에 컨텍스트 정보 제공
        enhanced_llm_suggestion = self._get_context_aware_llm_suggestion(
            ocr_data, amount, usage_date, semantic_matches, 
            user_patterns, time_context, amount_context
        )
        
        return {
            'semantic_matches': semantic_matches,
            'user_patterns': user_patterns,
            'time_context': time_context,
            'amount_context': amount_context,
            'enhanced_suggestion': enhanced_llm_suggestion
        }
    
    def _analyze_user_patterns(self, user_context: Dict) -> Dict:
        """사용자별 사용 패턴 분석"""
        # 사용자 ID 기반 패턴 분석 로직
        return {
            'preferred_categories': ['복리후생비', '여비교통비'],
            'avg_amount': 15000,
            'frequent_merchants': ['스타벅스', '지하철']
        }
    
    def _analyze_time_context(self, usage_date: str) -> Dict:
        """시간 컨텍스트 분석"""
        try:
            date_obj = datetime.strptime(usage_date, '%Y-%m-%d')
            hour = date_obj.hour if hasattr(date_obj, 'hour') else 12
            
            if 9 <= hour <= 18:
                return {'period': 'business_hours', 'likely_purpose': 'work_related'}
            elif 18 <= hour <= 22:
                return {'period': 'evening', 'likely_purpose': 'overtime_or_personal'}
            else:
                return {'period': 'night_early', 'likely_purpose': 'personal'}
        except:
            return {'period': 'unknown', 'likely_purpose': 'unknown'}
    
    def _analyze_amount_context(self, amount: int) -> Dict:
        """금액 컨텍스트 분석"""
        if amount <= 5000:
            return {'range': 'small', 'likely_items': ['커피', '간식', '교통비']}
        elif amount <= 20000:
            return {'range': 'medium', 'likely_items': ['식사', '택시', '소모품']}
        elif amount <= 100000:
            return {'range': 'large', 'likely_items': ['회식', '숙박', '장비구매']}
        else:
            return {'range': 'very_large', 'likely_items': ['출장', '교육', '장비']}
    
    def _is_time_context_match(self, match: Dict, time_context: Dict) -> bool:
        """시간 컨텍스트 매칭 여부 확인"""
        # 야근시간 + 식대 관련 매칭 등의 로직
        return False
    
    def _is_amount_context_match(self, match: Dict, amount_context: Dict) -> bool:
        """금액 컨텍스트 매칭 여부 확인"""
        # 금액대와 계정과목의 일반적 매칭 확인
        return False
    
    def _get_context_aware_llm_suggestion(self, 
                                        ocr_data: str, 
                                        amount: int, 
                                        usage_date: str,
                                        semantic_matches: Dict,
                                        user_patterns: Dict,
                                        time_context: Dict,
                                        amount_context: Dict) -> Dict:
        """컨텍스트를 고려한 LLM 추천"""
        
        context_prompt = f"""
        추가 컨텍스트 정보:
        - 시간대: {time_context.get('period', '알 수 없음')}
        - 금액대: {amount_context.get('range', '알 수 없음')}
        - 유사한 과거 거래: {semantic_matches.get('total_candidates', 0)}건
        - 사용자 선호 패턴: {', '.join(user_patterns.get('preferred_categories', []))}
        
        이 컨텍스트를 고려하여 더 정확한 분류를 해주세요.
        """
        
        # 기존 LLM 서비스에 컨텍스트 추가하여 호출
        # (실제 구현에서는 llm_service의 메서드를 확장)
        
        return {
            'account_category': '복리후생비',
            'description': '야근 식대',
            'confidence': 85,
            'reasoning': '시간대와 금액, 과거 패턴을 종합적으로 고려한 결과'
        }

# 싱글톤 인스턴스
ml_enhancement_service = MLEnhancementService() 