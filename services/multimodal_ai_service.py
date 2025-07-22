import logging
import asyncio
import json
import numpy as np
from typing import Dict, List, Optional, Union, AsyncGenerator
from datetime import datetime, timedelta
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import threading
import queue
from collections import deque

from services.ocr_service import ocr_service
from services.llm_service import llm_service
from services.db_service import db_service
from config.settings import Config

@dataclass
class MultimodalInput:
    """멀티모달 입력 데이터"""
    image_data: bytes
    audio_data: Optional[bytes] = None
    text_memo: Optional[str] = None
    location_data: Optional[Dict] = None
    timestamp: datetime = None
    user_context: Optional[Dict] = None

@dataclass
class StreamEvent:
    """실시간 스트림 이벤트"""
    event_id: str
    event_type: str
    data: Dict
    timestamp: datetime
    priority: int = 1

class MultimodalAIService:
    """차세대 멀티모달 AI 서비스"""
    
    def __init__(self):
        self.config = Config
        self.executor = ThreadPoolExecutor(max_workers=4)
        
        # 멀티모달 처리 가중치
        self.modality_weights = {
            'visual': 0.6,      # 이미지/OCR 정보
            'audio': 0.2,       # 음성 메모
            'text': 0.15,       # 텍스트 메모
            'context': 0.05     # 위치/시간 컨텍스트
        }
    
    async def process_multimodal_receipt(self, multimodal_input: MultimodalInput) -> Dict:
        """
        멀티모달 영수증 처리
        
        Args:
            multimodal_input: 이미지, 음성, 텍스트, 위치 등 다양한 입력
            
        Returns:
            Dict: 통합 분석 결과
        """
        logging.info("=== Starting Multimodal AI Processing ===")
        
        # 병렬 처리를 위한 태스크들
        tasks = []
        
        # 1. 시각적 정보 처리 (OCR + 이미지 분석)
        if multimodal_input.image_data:
            visual_task = asyncio.create_task(
                self._process_visual_modality(multimodal_input.image_data)
            )
            tasks.append(('visual', visual_task))
        
        # 2. 음성 정보 처리
        if multimodal_input.audio_data:
            audio_task = asyncio.create_task(
                self._process_audio_modality(multimodal_input.audio_data)
            )
            tasks.append(('audio', audio_task))
        
        # 3. 텍스트 메모 처리
        if multimodal_input.text_memo:
            text_task = asyncio.create_task(
                self._process_text_modality(multimodal_input.text_memo)
            )
            tasks.append(('text', text_task))
        
        # 4. 컨텍스트 정보 처리
        if multimodal_input.location_data or multimodal_input.user_context:
            context_task = asyncio.create_task(
                self._process_context_modality(
                    multimodal_input.location_data, 
                    multimodal_input.user_context,
                    multimodal_input.timestamp
                )
            )
            tasks.append(('context', context_task))
        
        # 모든 모달리티 병렬 처리
        modality_results = {}
        for modality_name, task in tasks:
            try:
                result = await task
                modality_results[modality_name] = result
                logging.info(f"✅ {modality_name} modality processed successfully")
            except Exception as e:
                logging.error(f"❌ {modality_name} modality failed: {e}")
                modality_results[modality_name] = {"error": str(e)}
        
        # 멀티모달 융합 및 최종 결과 생성
        fused_result = await self._fuse_multimodal_results(modality_results)
        
        logging.info("=== Multimodal AI Processing Completed ===")
        return fused_result
    
    async def _process_visual_modality(self, image_data: bytes) -> Dict:
        """시각적 모달리티 처리"""
        try:
            # OCR 처리
            ocr_result = await asyncio.get_event_loop().run_in_executor(
                self.executor, ocr_service.extract_text_from_image, image_data
            )
            
            # 텍스트 추출
            extracted_text = ocr_service.extract_text_blocks(ocr_result)
            
            # LLM으로 구조화
            structured_data = await asyncio.get_event_loop().run_in_executor(
                self.executor, llm_service.extract_structured_data, extracted_text
            )
            
            # 이미지 특성 분석 (예: 영수증 품질, 레이아웃 등)
            image_features = self._analyze_image_features(image_data)
            
            return {
                "ocr_result": ocr_result,
                "extracted_text": extracted_text,
                "structured_data": structured_data,
                "image_features": image_features,
                "confidence": self._calculate_visual_confidence(ocr_result, structured_data)
            }
            
        except Exception as e:
            logging.error(f"Visual modality processing failed: {e}")
            return {"error": str(e), "confidence": 0}
    
    async def _process_audio_modality(self, audio_data: bytes) -> Dict:
        """음성 모달리티 처리"""
        try:
            # 음성을 텍스트로 변환 (STT)
            transcribed_text = await self._speech_to_text(audio_data)
            
            if not transcribed_text:
                return {"error": "No speech detected", "confidence": 0}
            
            # 음성에서 추가 정보 추출
            audio_insights = await self._extract_audio_insights(transcribed_text)
            
            return {
                "transcribed_text": transcribed_text,
                "audio_insights": audio_insights,
                "confidence": self._calculate_audio_confidence(transcribed_text)
            }
            
        except Exception as e:
            logging.error(f"Audio modality processing failed: {e}")
            return {"error": str(e), "confidence": 0}
    
    async def _process_text_modality(self, text_memo: str) -> Dict:
        """텍스트 모달리티 처리"""
        try:
            # 텍스트에서 의도 및 추가 정보 추출
            text_analysis = await self._analyze_text_memo(text_memo)
            
            return {
                "original_text": text_memo,
                "analysis": text_analysis,
                "confidence": self._calculate_text_confidence(text_memo)
            }
            
        except Exception as e:
            logging.error(f"Text modality processing failed: {e}")
            return {"error": str(e), "confidence": 0}
    
    async def _process_context_modality(self, location_data: Optional[Dict], 
                                      user_context: Optional[Dict],
                                      timestamp: Optional[datetime]) -> Dict:
        """컨텍스트 모달리티 처리"""
        try:
            context_insights = {
                "location_analysis": {},
                "temporal_analysis": {},
                "user_pattern_analysis": {}
            }
            
            # 위치 정보 분석
            if location_data:
                context_insights["location_analysis"] = self._analyze_location_context(location_data)
            
            # 시간 정보 분석
            if timestamp:
                context_insights["temporal_analysis"] = self._analyze_temporal_context(timestamp)
            
            # 사용자 패턴 분석
            if user_context:
                context_insights["user_pattern_analysis"] = await self._analyze_user_context(user_context)
            
            return {
                "context_insights": context_insights,
                "confidence": self._calculate_context_confidence(context_insights)
            }
            
        except Exception as e:
            logging.error(f"Context modality processing failed: {e}")
            return {"error": str(e), "confidence": 0}
    
    async def _fuse_multimodal_results(self, modality_results: Dict) -> Dict:
        """멀티모달 결과 융합"""
        try:
            # 각 모달리티별 신뢰도 계산
            weighted_confidences = {}
            valid_modalities = []
            
            for modality, result in modality_results.items():
                if "error" not in result and modality in self.modality_weights:
                    confidence = result.get('confidence', 0)
                    weight = self.modality_weights[modality]
                    weighted_confidences[modality] = confidence * weight
                    valid_modalities.append(modality)
            
            if not valid_modalities:
                return {"error": "All modalities failed", "confidence": 0}
            
            # 기본 정보 (시각적 모달리티에서)
            base_data = {}
            if 'visual' in modality_results and 'structured_data' in modality_results['visual']:
                base_data = modality_results['visual']['structured_data']
            
            # 멀티모달 보완 정보
            enhanced_data = base_data.copy()
            
            # 음성에서 추가 정보 보완
            if 'audio' in modality_results and 'audio_insights' in modality_results['audio']:
                audio_insights = modality_results['audio']['audio_insights']
                if audio_insights.get('purpose'):
                    enhanced_data['enhanced_description'] = audio_insights['purpose']
                if audio_insights.get('additional_details'):
                    enhanced_data['additional_context'] = audio_insights['additional_details']
            
            # 텍스트 메모에서 추가 정보 보완
            if 'text' in modality_results and 'analysis' in modality_results['text']:
                text_analysis = modality_results['text']['analysis']
                if text_analysis.get('intent'):
                    enhanced_data['user_intent'] = text_analysis['intent']
            
            # 컨텍스트에서 추가 정보 보완
            if 'context' in modality_results and 'context_insights' in modality_results['context']:
                context_insights = modality_results['context']['context_insights']
                enhanced_data['context_analysis'] = context_insights
            
            # 최종 카테고리 및 설명 결정
            final_suggestion = await self._determine_final_category(enhanced_data, modality_results)
            
            # 종합 신뢰도 계산
            overall_confidence = sum(weighted_confidences.values())
            
            return {
                "multimodal_analysis": {
                    "base_data": base_data,
                    "enhanced_data": enhanced_data,
                    "modality_results": modality_results,
                    "valid_modalities": valid_modalities
                },
                "final_suggestion": final_suggestion,
                "confidence": min(100, int(overall_confidence)),
                "fusion_method": "weighted_confidence_fusion"
            }
            
        except Exception as e:
            logging.error(f"Multimodal fusion failed: {e}")
            return {"error": str(e), "confidence": 0}
    
    async def _speech_to_text(self, audio_data: bytes) -> Optional[str]:
        """음성을 텍스트로 변환 (STT)"""
        try:
            # 실제 환경에서는 OpenAI Whisper나 Google Speech-to-Text 사용
            # 현재는 시뮬레이션
            await asyncio.sleep(0.5)  # STT 처리 시뮬레이션
            
            # Mock 응답 (실제로는 audio_data를 STT 서비스로 전송)
            mock_transcriptions = [
                "이건 점심 식사비야",
                "야근하면서 시킨 치킨 값이야",
                "출장 중에 머문 숙박비",
                "회사 물품 구매했어",
                "커피 한 잔 샀어"
            ]
            
            # 실제 구현 예시:
            # response = await openai_whisper.transcribe(audio_data)
            # return response.text
            
            import random
            return random.choice(mock_transcriptions)
            
        except Exception as e:
            logging.error(f"Speech-to-text failed: {e}")
            return None
    
    async def _extract_audio_insights(self, transcribed_text: str) -> Dict:
        """음성에서 인사이트 추출"""
        try:
            # LLM을 사용해 음성 메모에서 의도와 추가 정보 추출
            prompt = f"""
            다음 음성 메모를 분석하여 영수증 분류에 도움이 되는 정보를 추출하세요:
            
            음성 메모: "{transcribed_text}"
            
            다음 정보를 JSON 형태로 추출하세요:
            {{
                "purpose": "사용 목적 (예: 점심 식사, 야근 식대, 출장 숙박)",
                "urgency": "긴급도 (low/medium/high)",
                "additional_details": "추가 세부사항",
                "emotional_tone": "감정 톤 (neutral/positive/negative)"
            }}
            """
            
            # 실제로는 LLM 호출
            await asyncio.sleep(0.3)
            
            # Mock 응답
            if "점심" in transcribed_text or "식사" in transcribed_text:
                return {
                    "purpose": "점심 식대",
                    "urgency": "low",
                    "additional_details": "일반적인 식사비",
                    "emotional_tone": "neutral"
                }
            elif "야근" in transcribed_text or "치킨" in transcribed_text:
                return {
                    "purpose": "야근 식대",
                    "urgency": "medium",
                    "additional_details": "야근 중 주문한 음식",
                    "emotional_tone": "neutral"
                }
            else:
                return {
                    "purpose": "기타 사용",
                    "urgency": "low",
                    "additional_details": "추가 정보 없음",
                    "emotional_tone": "neutral"
                }
                
        except Exception as e:
            logging.error(f"Audio insights extraction failed: {e}")
            return {}
    
    def _analyze_image_features(self, image_data: bytes) -> Dict:
        """이미지 특성 분석"""
        # 실제로는 이미지 품질, 크기, 회전 등을 분석
        image_size = len(image_data)
        
        return {
            "image_size_kb": round(image_size / 1024, 2),
            "estimated_quality": "high" if image_size > 100000 else "medium",
            "format_detected": "jpeg",  # 실제로는 이미지 헤더 분석
            "processing_difficulty": "easy"
        }
    
    def _calculate_visual_confidence(self, ocr_result: Dict, structured_data: Dict) -> float:
        """시각적 모달리티 신뢰도 계산"""
        confidence_factors = []
        
        # OCR 신뢰도
        if ocr_result:
            confidence_factors.append(80)  # 기본 OCR 신뢰도
        
        # 구조화된 데이터 완성도
        if structured_data:
            required_fields = ['merchant_name', 'total_price', 'transaction_date']
            filled_fields = sum(1 for field in required_fields if structured_data.get(field))
            completeness = (filled_fields / len(required_fields)) * 100
            confidence_factors.append(completeness)
        
        return np.mean(confidence_factors) if confidence_factors else 0
    
    def _calculate_audio_confidence(self, transcribed_text: str) -> float:
        """음성 모달리티 신뢰도 계산"""
        if not transcribed_text:
            return 0
        
        # 텍스트 길이와 키워드 기반 신뢰도
        text_length_score = min(100, len(transcribed_text) * 2)
        
        # 관련 키워드 존재 여부
        keywords = ['식사', '야근', '출장', '구매', '커피', '점심', '저녁']
        keyword_score = sum(30 for keyword in keywords if keyword in transcribed_text)
        
        return min(100, (text_length_score + keyword_score) / 2)
    
    def _calculate_text_confidence(self, text_memo: str) -> float:
        """텍스트 모달리티 신뢰도 계산"""
        if not text_memo:
            return 0
        
        return min(100, len(text_memo) * 3)  # 간단한 길이 기반 신뢰도
    
    def _calculate_context_confidence(self, context_insights: Dict) -> float:
        """컨텍스트 모달리티 신뢰도 계산"""
        available_contexts = sum(1 for insights in context_insights.values() if insights)
        return (available_contexts / 3) * 100  # 3가지 컨텍스트 중 사용 가능한 비율
    
    async def _analyze_text_memo(self, text_memo: str) -> Dict:
        """텍스트 메모 분석"""
        # 간단한 키워드 기반 분석
        analysis = {
            "intent": "unknown",
            "keywords": [],
            "sentiment": "neutral"
        }
        
        # 의도 파악
        if any(word in text_memo for word in ['식사', '점심', '저녁']):
            analysis["intent"] = "meal_expense"
        elif any(word in text_memo for word in ['출장', '여행']):
            analysis["intent"] = "travel_expense"
        elif any(word in text_memo for word in ['구매', '물품']):
            analysis["intent"] = "purchase"
        
        # 키워드 추출
        import re
        words = re.findall(r'[가-힣]+', text_memo)
        analysis["keywords"] = [word for word in words if len(word) >= 2]
        
        return analysis
    
    def _analyze_location_context(self, location_data: Dict) -> Dict:
        """위치 컨텍스트 분석"""
        return {
            "location_type": "business_district",  # GPS 기반 분석
            "nearby_businesses": ["restaurants", "office_buildings"],
            "distance_from_office": "near"  # 회사 위치와 비교
        }
    
    def _analyze_temporal_context(self, timestamp: datetime) -> Dict:
        """시간 컨텍스트 분석"""
        hour = timestamp.hour
        
        if 9 <= hour <= 12:
            period = "morning_work"
        elif 12 <= hour <= 14:
            period = "lunch_time"
        elif 14 <= hour <= 18:
            period = "afternoon_work"
        elif 18 <= hour <= 22:
            period = "evening_overtime"
        else:
            period = "off_hours"
        
        return {
            "time_period": period,
            "is_business_hours": 9 <= hour <= 18,
            "is_meal_time": hour in [12, 13, 18, 19],
            "day_of_week": timestamp.strftime("%A")
        }
    
    async def _analyze_user_context(self, user_context: Dict) -> Dict:
        """사용자 컨텍스트 분석"""
        # 사용자 역할, 부서, 과거 패턴 등 분석
        return {
            "user_role": user_context.get("role", "employee"),
            "department": user_context.get("department", "general"),
            "expense_patterns": await self._get_user_expense_patterns(user_context.get("user_id"))
        }
    
    async def _get_user_expense_patterns(self, user_id: Optional[str]) -> Dict:
        """사용자 지출 패턴 조회"""
        if not user_id:
            return {}
        
        # 실제로는 DB에서 사용자별 패턴 조회
        return {
            "frequent_categories": ["복리후생비", "여비교통비"],
            "average_amount": 15000,
            "preferred_time": "lunch_time"
        }
    
    async def _determine_final_category(self, enhanced_data: Dict, modality_results: Dict) -> Dict:
        """최종 카테고리 및 설명 결정"""
        try:
            # 모든 모달리티의 정보를 종합하여 최종 결정
            base_category = enhanced_data.get('account_category', '소모품비')
            base_description = enhanced_data.get('description', '기타')
            
            # 음성/텍스트에서 더 구체적인 정보가 있으면 우선 적용
            if 'enhanced_description' in enhanced_data:
                base_description = enhanced_data['enhanced_description']
            
            # 컨텍스트 기반 보정
            if 'context_analysis' in enhanced_data:
                context = enhanced_data['context_analysis']
                temporal = context.get('temporal_analysis', {})
                
                if temporal.get('is_meal_time') and '식' not in base_description:
                    if temporal.get('time_period') == 'lunch_time':
                        base_description = f"점심 {base_description}"
                    elif temporal.get('time_period') == 'evening_overtime':
                        base_description = f"야근 {base_description}"
            
            return {
                "account_category": base_category,
                "description": base_description,
                "reasoning": "multimodal_fusion_analysis",
                "enhancement_source": "multimodal_ai"
            }
            
        except Exception as e:
            logging.error(f"Final category determination failed: {e}")
            return {
                "account_category": "소모품비",
                "description": "기타",
                "reasoning": "fallback_due_to_error"
            }

# 연합학습 관리자
class FederatedLearningManager:
    """연합학습 관리 서비스"""
    
    def __init__(self):
        self.participant_models = {}
        self.global_model_version = 0
        self.learning_rounds = 0
    
    async def aggregate_user_patterns(self, user_patterns: List[Dict]) -> Dict:
        """
        사용자 패턴들을 연합학습으로 집계
        개인정보는 보호하면서 전체 패턴 학습
        """
        try:
            logging.info("Starting federated learning aggregation")
            
            # 개인정보 제거 및 패턴만 추출
            anonymized_patterns = []
            for pattern in user_patterns:
                anonymized = self._anonymize_pattern(pattern)
                if anonymized:
                    anonymized_patterns.append(anonymized)
            
            if not anonymized_patterns:
                return {"error": "No valid patterns for aggregation"}
            
            # 패턴 집계 (연합 평균)
            aggregated_patterns = self._federated_average(anonymized_patterns)
            
            # 글로벌 모델 업데이트
            self.global_model_version += 1
            self.learning_rounds += 1
            
            global_insights = {
                "model_version": self.global_model_version,
                "learning_round": self.learning_rounds,
                "participant_count": len(anonymized_patterns),
                "aggregated_patterns": aggregated_patterns,
                "privacy_preserved": True,
                "updated_at": datetime.now().isoformat()
            }
            
            logging.info(f"Federated learning completed: v{self.global_model_version}")
            return global_insights
            
        except Exception as e:
            logging.error(f"Federated learning failed: {e}")
            return {"error": str(e)}
    
    def _anonymize_pattern(self, user_pattern: Dict) -> Optional[Dict]:
        """사용자 패턴에서 개인정보 제거"""
        try:
            # 개인식별정보 제거하고 패턴만 추출
            anonymized = {
                "category_preferences": user_pattern.get("preferred_categories", []),
                "amount_range": self._categorize_amount_range(user_pattern.get("avg_amount", 0)),
                "time_patterns": user_pattern.get("time_preferences", []),
                "merchant_types": self._categorize_merchant_types(user_pattern.get("frequent_merchants", []))
            }
            
            return anonymized if any(anonymized.values()) else None
            
        except Exception:
            return None
    
    def _categorize_amount_range(self, amount: float) -> str:
        """금액을 범위로 카테고리화"""
        if amount <= 5000:
            return "very_low"
        elif amount <= 15000:
            return "low"
        elif amount <= 50000:
            return "medium"
        elif amount <= 200000:
            return "high"
        else:
            return "very_high"
    
    def _categorize_merchant_types(self, merchants: List[str]) -> List[str]:
        """상점들을 업종별로 카테고리화"""
        merchant_categories = []
        
        for merchant in merchants:
            if any(keyword in merchant.lower() for keyword in ['카페', '커피', '스타벅스']):
                merchant_categories.append("cafe")
            elif any(keyword in merchant.lower() for keyword in ['마트', '슈퍼']):
                merchant_categories.append("mart")
            elif any(keyword in merchant.lower() for keyword in ['주유소', '셀프']):
                merchant_categories.append("gas_station")
            # ... 더 많은 카테고리
        
        return list(set(merchant_categories))
    
    def _federated_average(self, patterns: List[Dict]) -> Dict:
        """연합 평균 계산"""
        try:
            # 카테고리별 선호도 집계
            all_categories = []
            amount_ranges = []
            merchant_types = []
            
            for pattern in patterns:
                all_categories.extend(pattern.get("category_preferences", []))
                amount_ranges.append(pattern.get("amount_range", "unknown"))
                merchant_types.extend(pattern.get("merchant_types", []))
            
            # 빈도 기반 집계
            from collections import Counter
            
            category_distribution = dict(Counter(all_categories))
            amount_distribution = dict(Counter(amount_ranges))
            merchant_distribution = dict(Counter(merchant_types))
            
            return {
                "popular_categories": category_distribution,
                "amount_distribution": amount_distribution,
                "merchant_type_distribution": merchant_distribution,
                "total_patterns": len(patterns)
            }
            
        except Exception as e:
            logging.error(f"Federated averaging failed: {e}")
            return {}

# 실시간 스트림 처리기
class RealTimeStreamProcessor:
    """실시간 영수증 스트림 처리"""
    
    def __init__(self):
        self.event_queue = queue.Queue(maxsize=1000)
        self.processing_thread = None
        self.is_running = False
        self.metrics = {
            "processed_count": 0,
            "error_count": 0,
            "avg_processing_time": 0,
            "last_processed": None
        }
    
    def start_stream_processing(self):
        """스트림 처리 시작"""
        if self.is_running:
            logging.warning("Stream processor already running")
            return
        
        self.is_running = True
        self.processing_thread = threading.Thread(target=self._process_stream_worker)
        self.processing_thread.daemon = True
        self.processing_thread.start()
        
        logging.info("Real-time stream processor started")
    
    def stop_stream_processing(self):
        """스트림 처리 중지"""
        self.is_running = False
        if self.processing_thread:
            self.processing_thread.join(timeout=5)
        
        logging.info("Real-time stream processor stopped")
    
    def add_receipt_event(self, receipt_data: Dict, priority: int = 1) -> bool:
        """영수증 이벤트를 스트림에 추가"""
        try:
            event = StreamEvent(
                event_id=f"receipt_{datetime.now().timestamp()}",
                event_type="receipt_processing",
                data=receipt_data,
                timestamp=datetime.now(),
                priority=priority
            )
            
            # 우선순위가 높으면 큐 앞쪽에 삽입 (간단한 구현)
            if priority > 5:
                # 높은 우선순위: 즉시 처리
                self._process_single_event(event)
            else:
                # 일반 우선순위: 큐에 추가
                self.event_queue.put(event, timeout=1)
            
            return True
            
        except queue.Full:
            logging.warning("Event queue is full, dropping event")
            return False
        except Exception as e:
            logging.error(f"Failed to add receipt event: {e}")
            return False
    
    def _process_stream_worker(self):
        """스트림 처리 워커 스레드"""
        logging.info("Stream processing worker started")
        
        while self.is_running:
            try:
                # 큐에서 이벤트 가져오기 (타임아웃 1초)
                event = self.event_queue.get(timeout=1)
                
                # 이벤트 처리
                self._process_single_event(event)
                
                # 큐 태스크 완료 표시
                self.event_queue.task_done()
                
            except queue.Empty:
                # 타임아웃 - 계속 진행
                continue
            except Exception as e:
                logging.error(f"Stream processing error: {e}")
                self.metrics["error_count"] += 1
    
    def _process_single_event(self, event: StreamEvent):
        """단일 이벤트 처리"""
        start_time = datetime.now()
        
        try:
            logging.info(f"Processing event: {event.event_id}")
            
            # 실제 영수증 처리 로직
            if event.event_type == "receipt_processing":
                result = self._process_receipt_stream_data(event.data)
                
                # 결과 저장 또는 알림 전송
                self._handle_processing_result(event, result)
            
            # 메트릭 업데이트
            processing_time = (datetime.now() - start_time).total_seconds()
            self._update_metrics(processing_time)
            
            logging.info(f"Event processed successfully: {event.event_id}")
            
        except Exception as e:
            logging.error(f"Failed to process event {event.event_id}: {e}")
            self.metrics["error_count"] += 1
    
    def _process_receipt_stream_data(self, receipt_data: Dict) -> Dict:
        """스트림 영수증 데이터 처리"""
        # 실시간 처리에 최적화된 간단한 로직
        try:
            # 기본 OCR 및 분류 (캐시 우선 활용)
            from services.cache_service import cache_service
            
            # 이미지 해시 생성
            image_data = receipt_data.get('image_data')
            if image_data:
                image_hash = cache_service.generate_image_hash(image_data)
                
                # 캐시에서 먼저 확인
                cached_result = cache_service.get_cached_ocr_result(image_hash)
                if cached_result:
                    return {"result": "processed_from_cache", "cached": True}
            
            # 새로운 처리
            return {"result": "processed_realtime", "cached": False}
            
        except Exception as e:
            return {"error": str(e)}
    
    def _handle_processing_result(self, event: StreamEvent, result: Dict):
        """처리 결과 핸들링"""
        try:
            # 결과에 따른 후속 작업
            if "error" in result:
                logging.error(f"Event {event.event_id} processing failed")
                # 에러 알림 등
            else:
                logging.info(f"Event {event.event_id} processed successfully")
                # 성공 알림, 대시보드 업데이트 등
            
            # 실시간 메트릭 업데이트
            self._trigger_realtime_alerts_if_needed(result)
            
        except Exception as e:
            logging.error(f"Result handling failed: {e}")
    
    def _update_metrics(self, processing_time: float):
        """메트릭 업데이트"""
        self.metrics["processed_count"] += 1
        self.metrics["last_processed"] = datetime.now().isoformat()
        
        # 이동 평균으로 처리 시간 업데이트
        current_avg = self.metrics["avg_processing_time"]
        count = self.metrics["processed_count"]
        
        self.metrics["avg_processing_time"] = (
            (current_avg * (count - 1) + processing_time) / count
        )
    
    def _trigger_realtime_alerts_if_needed(self, result: Dict):
        """실시간 알림 트리거"""
        # 특정 조건에서 알림 발송
        try:
            if self.metrics["error_count"] > 10:
                logging.warning("High error rate detected in stream processing")
                # 슬랙, 이메일 등으로 알림
            
            if self.metrics["avg_processing_time"] > 5.0:
                logging.warning("High processing latency detected")
                # 성능 알림
            
        except Exception as e:
            logging.error(f"Alert triggering failed: {e}")
    
    def get_stream_metrics(self) -> Dict:
        """스트림 처리 메트릭 조회"""
        return {
            **self.metrics,
            "queue_size": self.event_queue.qsize(),
            "is_running": self.is_running,
            "error_rate": (
                self.metrics["error_count"] / max(1, self.metrics["processed_count"]) * 100
            )
        }

# 싱글톤 인스턴스들
multimodal_ai_service = MultimodalAIService()
federated_learning_manager = FederatedLearningManager()
stream_processor = RealTimeStreamProcessor() 