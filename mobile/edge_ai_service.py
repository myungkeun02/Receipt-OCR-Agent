import logging
import json
import base64
import hashlib
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
import asyncio

# Edge AI 관련 임포트 (실제 환경에서는 TensorFlow Lite, ONNX 등)
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    logging.warning("NumPy not available - edge AI features limited")

@dataclass
class OfflineReceiptData:
    """오프라인 영수증 데이터"""
    receipt_id: str
    image_data: bytes
    extracted_text: Optional[str]
    basic_analysis: Optional[Dict]
    timestamp: datetime
    sync_status: str = "pending"  # pending, synced, failed

class EdgeAIProcessor:
    """엣지 AI 처리기 - 모바일 디바이스에서 경량 처리"""
    
    def __init__(self):
        self.model_loaded = False
        self.offline_cache = {}
        self.processing_stats = {
            "processed_count": 0,
            "avg_processing_time": 0,
            "cache_hits": 0
        }
        
        # 경량 모델 초기화 시뮬레이션
        self._initialize_lightweight_models()
    
    def _initialize_lightweight_models(self):
        """경량화된 AI 모델 초기화"""
        try:
            # 실제 환경에서는 TensorFlow Lite 또는 ONNX 모델 로드
            # self.ocr_model = tflite.Interpreter("receipt_ocr.tflite")
            # self.classifier_model = onnx.InferenceSession("category_classifier.onnx")
            
            # 현재는 시뮬레이션
            self.edge_models = {
                "text_extractor": self._create_mock_text_extractor(),
                "category_classifier": self._create_mock_classifier(),
                "quality_assessor": self._create_mock_quality_assessor()
            }
            
            self.model_loaded = True
            logging.info("Edge AI models initialized successfully")
            
        except Exception as e:
            logging.error(f"Edge AI model initialization failed: {e}")
            self.model_loaded = False
    
    def _create_mock_text_extractor(self):
        """Mock 텍스트 추출기"""
        return {
            "type": "lightweight_ocr",
            "accuracy": 0.85,
            "speed": "fast"
        }
    
    def _create_mock_classifier(self):
        """Mock 분류기"""
        return {
            "type": "neural_classifier",
            "categories": ["복리후생비", "여비교통비", "소모품비", "접대비"],
            "confidence_threshold": 0.7
        }
    
    def _create_mock_quality_assessor(self):
        """Mock 품질 평가기"""
        return {
            "type": "image_quality_cnn",
            "metrics": ["blur", "brightness", "angle", "completeness"]
        }
    
    async def process_receipt_offline(self, image_data: bytes, 
                                    device_context: Optional[Dict] = None) -> Dict:
        """
        오프라인 영수증 처리 (네트워크 없이 로컬 처리)
        
        Args:
            image_data: 영수증 이미지 데이터
            device_context: 디바이스 컨텍스트 (위치, 시간 등)
            
        Returns:
            Dict: 오프라인 처리 결과
        """
        start_time = datetime.now()
        
        try:
            logging.info("Starting offline receipt processing")
            
            if not self.model_loaded:
                return {
                    "success": False,
                    "error": "Edge AI models not available",
                    "offline_capable": False
                }
            
            # 1. 이미지 품질 평가
            quality_assessment = await self._assess_image_quality(image_data)
            
            if quality_assessment["quality_score"] < 0.5:
                return {
                    "success": False,
                    "error": "Image quality too low for offline processing",
                    "quality_assessment": quality_assessment,
                    "recommendation": "Use online processing or retake photo"
                }
            
            # 2. 경량 OCR 처리
            extracted_text = await self._lightweight_ocr_extraction(image_data)
            
            # 3. 기본 분류
            basic_classification = await self._lightweight_classification(
                extracted_text, device_context
            )
            
            # 4. 오프라인 결과 생성
            offline_result = {
                "success": True,
                "extracted_text": extracted_text,
                "basic_classification": basic_classification,
                "quality_assessment": quality_assessment,
                "processing_mode": "offline_edge_ai",
                "confidence": self._calculate_offline_confidence(
                    quality_assessment, extracted_text, basic_classification
                ),
                "requires_online_refinement": basic_classification.get("confidence", 0) < 0.8,
                "processed_at": datetime.now().isoformat(),
                "device_context": device_context
            }
            
            # 5. 오프라인 캐시에 저장
            receipt_id = self._generate_receipt_id(image_data)
            await self._cache_offline_result(receipt_id, offline_result, image_data)
            
            # 6. 처리 통계 업데이트
            processing_time = (datetime.now() - start_time).total_seconds()
            self._update_processing_stats(processing_time)
            
            logging.info(f"Offline processing completed in {processing_time:.2f}s")
            return offline_result
            
        except Exception as e:
            logging.error(f"Offline receipt processing failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "offline_capable": True,
                "fallback_available": True
            }
    
    async def _assess_image_quality(self, image_data: bytes) -> Dict:
        """이미지 품질 평가"""
        try:
            # 실제 환경에서는 CNN 모델로 품질 평가
            await asyncio.sleep(0.1)  # 처리 시뮬레이션
            
            # 이미지 크기 기반 기본 평가
            image_size = len(image_data)
            
            # Mock 품질 평가
            quality_factors = {
                "size_score": min(1.0, image_size / 100000),  # 100KB 기준
                "estimated_blur": 0.1,  # 낮을수록 선명
                "estimated_brightness": 0.7,  # 0~1
                "estimated_completeness": 0.9  # 영수증 완성도
            }
            
            # 종합 품질 점수
            quality_score = (
                quality_factors["size_score"] * 0.3 +
                (1 - quality_factors["estimated_blur"]) * 0.3 +
                quality_factors["estimated_brightness"] * 0.2 +
                quality_factors["estimated_completeness"] * 0.2
            )
            
            return {
                "quality_score": round(quality_score, 3),
                "quality_factors": quality_factors,
                "assessment_method": "edge_ai_cnn",
                "recommendations": self._generate_quality_recommendations(quality_factors)
            }
            
        except Exception as e:
            logging.error(f"Image quality assessment failed: {e}")
            return {"quality_score": 0.5, "error": str(e)}
    
    def _generate_quality_recommendations(self, quality_factors: Dict) -> List[str]:
        """품질 개선 권장사항 생성"""
        recommendations = []
        
        if quality_factors["size_score"] < 0.5:
            recommendations.append("이미지 해상도를 높여주세요")
        
        if quality_factors["estimated_blur"] > 0.3:
            recommendations.append("카메라 초점을 맞춰주세요")
        
        if quality_factors["estimated_brightness"] < 0.3:
            recommendations.append("조명을 밝게 해주세요")
        elif quality_factors["estimated_brightness"] > 0.9:
            recommendations.append("조명이 너무 밝습니다")
        
        if quality_factors["estimated_completeness"] < 0.7:
            recommendations.append("영수증 전체가 보이도록 촬영해주세요")
        
        return recommendations
    
    async def _lightweight_ocr_extraction(self, image_data: bytes) -> str:
        """경량 OCR 텍스트 추출"""
        try:
            # 실제 환경에서는 TensorFlow Lite OCR 모델 사용
            await asyncio.sleep(0.3)  # 처리 시뮬레이션
            
            # Mock OCR 결과
            mock_texts = [
                "스타벅스 강남점\n2025-01-15\n아메리카노 4,500원\n카드결제",
                "이마트 본점\n2025-01-15\n생필품 구매 15,300원\n현금결제",
                "지하철 교통카드\n2025-01-15\n1,490원\n충전",
                "맥도날드 종로점\n2025-01-15\n빅맥세트 7,200원\n카드결제"
            ]
            
            # 실제로는 이미지 데이터를 OCR 모델에 입력
            import random
            extracted_text = random.choice(mock_texts)
            
            logging.info("Lightweight OCR extraction completed")
            return extracted_text
            
        except Exception as e:
            logging.error(f"Lightweight OCR failed: {e}")
            return ""
    
    async def _lightweight_classification(self, extracted_text: str, 
                                        device_context: Optional[Dict]) -> Dict:
        """경량 분류 처리"""
        try:
            # 실제 환경에서는 경량화된 분류 모델 사용
            await asyncio.sleep(0.2)  # 처리 시뮬레이션
            
            # 간단한 키워드 기반 분류
            classification_rules = {
                "복리후생비": ["스타벅스", "카페", "커피", "음료", "식당", "맥도날드"],
                "여비교통비": ["지하철", "버스", "택시", "교통카드", "주유소", "항공"],
                "소모품비": {
                    "마트": "생필품 구매", "편의점": "소모품 구매",
                    "문구": "사무용품", "이마트": "생필품 구매"
                },
                "접대비": {
                    "회식": "회식비", "술집": "접대비", "호텔": "접대비"
                }
            }
            
            text_lower = extracted_text.lower()
            classification_scores = {}
            
            for category, keywords in classification_rules.items():
                score = sum(1 for keyword in keywords if keyword in text_lower)
                if score > 0:
                    classification_scores[category] = score / len(keywords)
            
            # 최고 점수 카테고리 선택
            if classification_scores:
                best_category = max(classification_scores.items(), key=lambda x: x[1])
                category, confidence = best_category
            else:
                category, confidence = "소모품비", 0.3
            
            # 디바이스 컨텍스트 고려
            if device_context:
                category, confidence = self._adjust_with_device_context(
                    category, confidence, device_context
                )
            
            # 설명 생성
            description = self._generate_basic_description(extracted_text, category)
            
            return {
                "account_category": category,
                "description": description,
                "confidence": round(confidence, 3),
                "classification_method": "lightweight_keyword_ml",
                "all_scores": classification_scores
            }
            
        except Exception as e:
            logging.error(f"Lightweight classification failed: {e}")
            return {
                "account_category": "소모품비",
                "description": "기타",
                "confidence": 0.1,
                "error": str(e)
            }
    
    def _adjust_with_device_context(self, category: str, confidence: float, 
                                  device_context: Dict) -> tuple:
        """디바이스 컨텍스트로 분류 조정"""
        try:
            # 시간 컨텍스트
            if "timestamp" in device_context:
                hour = datetime.fromisoformat(device_context["timestamp"]).hour
                
                # 점심시간 (12-14시)에 식당 관련이면 복리후생비 가능성 증가
                if 12 <= hour <= 14 and "복리후생비" in category:
                    confidence = min(1.0, confidence * 1.2)
                
                # 야간시간 (18시 이후)에 식당이면 접대비 가능성 고려
                elif hour >= 18 and any(keyword in category for keyword in ["복리후생비"]):
                    # 컨텍스트에 따라 접대비로 재분류 고려
                    pass
            
            # 위치 컨텍스트
            if "location" in device_context:
                location_type = device_context["location"].get("type", "")
                if location_type == "office_area" and category == "복리후생비":
                    confidence = min(1.0, confidence * 1.1)
            
            return category, confidence
            
        except Exception as e:
            logging.error(f"Context adjustment failed: {e}")
            return category, confidence
    
    def _generate_basic_description(self, extracted_text: str, category: str) -> str:
        """기본 설명 생성"""
        try:
            text_lower = extracted_text.lower()
            
            # 카테고리별 기본 설명 매핑
            description_mapping = {
                "복리후생비": {
                    "커피": "커피", "스타벅스": "커피", "카페": "카페",
                    "맥도날드": "식사", "식당": "식사", "점심": "점심 식대"
                },
                "여비교통비": {
                    "지하철": "교통비", "버스": "교통비", "택시": "택시비",
                    "주유소": "주유비", "교통카드": "교통비"
                },
                "소모품비": {
                    "마트": "생필품 구매", "편의점": "소모품 구매",
                    "문구": "사무용품", "이마트": "생필품 구매"
                },
                "접대비": {
                    "회식": "회식비", "술집": "접대비", "호텔": "접대비"
                }
            }
            
            category_keywords = description_mapping.get(category, {})
            
            for keyword, description in category_keywords.items():
                if keyword in text_lower:
                    return description
            
            # 기본값
            return {
                "복리후생비": "식대",
                "여비교통비": "교통비",
                "소모품비": "구매",
                "접대비": "접대"
            }.get(category, "기타")
            
        except Exception:
            return "기타"
    
    def _calculate_offline_confidence(self, quality_assessment: Dict, 
                                    extracted_text: str, 
                                    classification: Dict) -> float:
        """오프라인 처리 전체 신뢰도 계산"""
        try:
            # 품질 점수
            quality_score = quality_assessment.get("quality_score", 0.5)
            
            # 텍스트 추출 신뢰도
            text_confidence = min(1.0, len(extracted_text) / 50) if extracted_text else 0
            
            # 분류 신뢰도
            classification_confidence = classification.get("confidence", 0)
            
            # 가중 평균
            overall_confidence = (
                quality_score * 0.3 +
                text_confidence * 0.3 +
                classification_confidence * 0.4
            )
            
            return round(overall_confidence, 3)
            
        except Exception:
            return 0.5
    
    def _generate_receipt_id(self, image_data: bytes) -> str:
        """영수증 ID 생성"""
        return f"receipt_{hashlib.md5(image_data).hexdigest()[:12]}_{int(datetime.now().timestamp())}"
    
    async def _cache_offline_result(self, receipt_id: str, result: Dict, image_data: bytes):
        """오프라인 결과 캐싱"""
        try:
            offline_data = OfflineReceiptData(
                receipt_id=receipt_id,
                image_data=image_data,
                extracted_text=result.get("extracted_text"),
                basic_analysis=result.get("basic_classification"),
                timestamp=datetime.now()
            )
            
            self.offline_cache[receipt_id] = offline_data
            
            # 캐시 크기 제한 (최대 100개)
            if len(self.offline_cache) > 100:
                # 가장 오래된 항목 제거
                oldest_id = min(self.offline_cache.keys(), 
                              key=lambda k: self.offline_cache[k].timestamp)
                del self.offline_cache[oldest_id]
            
            logging.info(f"Offline result cached: {receipt_id}")
            
        except Exception as e:
            logging.error(f"Offline caching failed: {e}")
    
    def _update_processing_stats(self, processing_time: float):
        """처리 통계 업데이트"""
        self.processing_stats["processed_count"] += 1
        
        # 이동 평균으로 처리 시간 업데이트
        count = self.processing_stats["processed_count"]
        current_avg = self.processing_stats["avg_processing_time"]
        
        self.processing_stats["avg_processing_time"] = (
            (current_avg * (count - 1) + processing_time) / count
        )
    
    async def sync_offline_data(self, network_available: bool = True) -> Dict:
        """오프라인 데이터를 온라인 서버와 동기화"""
        if not network_available:
            return {
                "synced_count": 0,
                "pending_count": len(self.offline_cache),
                "status": "network_unavailable"
            }
        
        try:
            synced_count = 0
            failed_count = 0
            
            for receipt_id, offline_data in list(self.offline_cache.items()):
                if offline_data.sync_status == "pending":
                    try:
                        # 온라인 서버로 전송
                        sync_result = await self._sync_single_receipt(offline_data)
                        
                        if sync_result["success"]:
                            offline_data.sync_status = "synced"
                            synced_count += 1
                        else:
                            offline_data.sync_status = "failed"
                            failed_count += 1
                        
                    except Exception as e:
                        logging.error(f"Sync failed for {receipt_id}: {e}")
                        offline_data.sync_status = "failed"
                        failed_count += 1
            
            # 동기화 완료된 항목들 정리
            self._cleanup_synced_data()
            
            return {
                "synced_count": synced_count,
                "failed_count": failed_count,
                "remaining_count": len([d for d in self.offline_cache.values() 
                                      if d.sync_status == "pending"]),
                "status": "completed"
            }
            
        except Exception as e:
            logging.error(f"Offline data sync failed: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _sync_single_receipt(self, offline_data: OfflineReceiptData) -> Dict:
        """단일 영수증 동기화"""
        try:
            # 실제 환경에서는 서버 API 호출
            await asyncio.sleep(0.5)  # 네트워크 지연 시뮬레이션
            
            # Mock 동기화 결과
            return {
                "success": True,
                "server_receipt_id": f"server_{offline_data.receipt_id}",
                "enhanced_result": {
                    "account_category": offline_data.basic_analysis.get("account_category"),
                    "description": offline_data.basic_analysis.get("description"),
                    "server_confidence": 0.92,  # 서버에서 향상된 신뢰도
                    "sync_timestamp": datetime.now().isoformat()
                }
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _cleanup_synced_data(self):
        """동기화 완료된 데이터 정리"""
        try:
            # 일주일 이상 된 동기화 완료 데이터 제거
            week_ago = datetime.now() - timedelta(days=7)
            
            to_remove = [
                receipt_id for receipt_id, data in self.offline_cache.items()
                if data.sync_status == "synced" and data.timestamp < week_ago
            ]
            
            for receipt_id in to_remove:
                del self.offline_cache[receipt_id]
            
            logging.info(f"Cleaned up {len(to_remove)} synced items")
            
        except Exception as e:
            logging.error(f"Cleanup failed: {e}")
    
    def get_offline_stats(self) -> Dict:
        """오프라인 처리 통계"""
        pending_count = len([d for d in self.offline_cache.values() if d.sync_status == "pending"])
        synced_count = len([d for d in self.offline_cache.values() if d.sync_status == "synced"])
        failed_count = len([d for d in self.offline_cache.values() if d.sync_status == "failed"])
        
        return {
            **self.processing_stats,
            "offline_cache": {
                "total_items": len(self.offline_cache),
                "pending_sync": pending_count,
                "synced": synced_count,
                "failed": failed_count
            },
            "model_status": {
                "loaded": self.model_loaded,
                "available_models": list(self.edge_models.keys()) if self.model_loaded else []
            }
        }

class PWAService:
    """Progressive Web App 서비스"""
    
    def __init__(self):
        self.cache_version = "v1.0.0"
        self.offline_capabilities = [
            "receipt_capture",
            "basic_processing", 
            "data_storage",
            "sync_queue"
        ]
    
    def generate_service_worker_config(self) -> Dict:
        """Service Worker 설정 생성"""
        return {
            "cache_name": f"receipt-app-{self.cache_version}",
            "offline_fallback": "/offline.html",
            "cache_strategies": {
                "api_calls": "cache_first",
                "static_assets": "cache_first",
                "images": "cache_first",
                "documents": "network_first"
            },
            "background_sync": {
                "enabled": True,
                "tag": "receipt-sync",
                "fallback_interval": 300000  # 5분
            },
            "push_notifications": {
                "enabled": True,
                "vapid_public_key": "placeholder_vapid_key"
            }
        }
    
    def generate_manifest_json(self) -> Dict:
        """PWA Manifest 생성"""
        return {
            "name": "Smart Receipt Processor",
            "short_name": "ReceiptAI",
            "description": "AI 기반 영수증 인식 및 분류 앱",
            "version": self.cache_version,
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#007bff",
            "orientation": "portrait",
            "icons": [
                {
                    "src": "/icons/icon-72x72.png",
                    "sizes": "72x72",
                    "type": "image/png"
                },
                {
                    "src": "/icons/icon-144x144.png", 
                    "sizes": "144x144",
                    "type": "image/png"
                },
                {
                    "src": "/icons/icon-512x512.png",
                    "sizes": "512x512", 
                    "type": "image/png",
                    "purpose": "maskable"
                }
            ],
            "categories": ["productivity", "business", "finance"],
            "screenshots": [
                {
                    "src": "/screenshots/mobile-1.png",
                    "sizes": "390x844",
                    "type": "image/png",
                    "platform": "mobile"
                }
            ],
            "features": [
                "camera_access",
                "offline_processing", 
                "background_sync",
                "push_notifications"
            ]
        }
    
    async def handle_offline_request(self, request_data: Dict) -> Dict:
        """오프라인 요청 처리"""
        try:
            request_type = request_data.get("type", "unknown")
            
            if request_type == "receipt_process":
                # 오프라인 영수증 처리
                return await self._handle_offline_receipt_processing(request_data)
            
            elif request_type == "data_query":
                # 캐시된 데이터 조회
                return await self._handle_offline_data_query(request_data)
            
            elif request_type == "sync_check":
                # 동기화 상태 확인
                return await self._handle_sync_status_check()
            
            else:
                return {
                    "success": False,
                    "error": "Unsupported offline request type",
                    "available_types": ["receipt_process", "data_query", "sync_check"]
                }
                
        except Exception as e:
            logging.error(f"Offline request handling failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _handle_offline_receipt_processing(self, request_data: Dict) -> Dict:
        """오프라인 영수증 처리 요청"""
        try:
            image_data = base64.b64decode(request_data.get("image_base64", ""))
            device_context = request_data.get("device_context", {})
            
            # Edge AI로 처리
            result = await edge_ai_processor.process_receipt_offline(
                image_data, device_context
            )
            
            return {
                "success": True,
                "result": result,
                "offline_processed": True,
                "sync_required": result.get("requires_online_refinement", False)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _handle_offline_data_query(self, request_data: Dict) -> Dict:
        """오프라인 데이터 조회"""
        try:
            query_type = request_data.get("query_type", "recent_receipts")
            
            if query_type == "recent_receipts":
                # 최근 영수증 목록
                recent_data = [
                    {
                        "id": receipt_id,
                        "timestamp": data.timestamp.isoformat(),
                        "sync_status": data.sync_status,
                        "extracted_text": data.extracted_text[:50] + "..." if data.extracted_text else None
                    }
                    for receipt_id, data in edge_ai_processor.offline_cache.items()
                ]
                
                return {"success": True, "data": recent_data}
            
            else:
                return {"success": False, "error": "Unsupported query type"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _handle_sync_status_check(self) -> Dict:
        """동기화 상태 확인"""
        try:
            stats = edge_ai_processor.get_offline_stats()
            
            return {
                "success": True,
                "sync_status": {
                    "pending_items": stats["offline_cache"]["pending_sync"],
                    "total_cached": stats["offline_cache"]["total_items"],
                    "last_sync": datetime.now().isoformat(),  # 실제로는 마지막 동기화 시간
                    "network_required": stats["offline_cache"]["pending_sync"] > 0
                }
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}

# 모바일 최적화 통합 서비스
class MobileOptimizationService:
    """모바일 최적화 통합 서비스"""
    
    def __init__(self):
        self.edge_ai = EdgeAIProcessor()
        self.pwa_service = PWAService()
        self.mobile_config = {
            "max_image_size_mb": 5,
            "compression_quality": 0.8,
            "offline_storage_limit_mb": 50,
            "sync_interval_minutes": 15
        }
    
    async def optimize_for_mobile(self, request_data: Dict) -> Dict:
        """모바일 최적화 처리"""
        try:
            # 네트워크 상태 확인
            network_available = request_data.get("network_available", True)
            device_info = request_data.get("device_info", {})
            
            if network_available:
                # 온라인 처리 + 캐싱
                return await self._handle_online_optimized(request_data)
            else:
                # 오프라인 처리
                return await self._handle_offline_optimized(request_data)
                
        except Exception as e:
            logging.error(f"Mobile optimization failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _handle_online_optimized(self, request_data: Dict) -> Dict:
        """온라인 최적화 처리"""
        # 온라인에서도 엣지 AI 우선 활용하여 속도 향상
        try:
            image_data = base64.b64decode(request_data.get("image_base64", ""))
            
            # 1. 엣지 AI로 빠른 초기 처리
            edge_result = await self.edge_ai.process_receipt_offline(
                image_data, request_data.get("device_context")
            )
            
            # 2. 높은 신뢰도면 엣지 결과 사용, 낮으면 서버 처리
            if edge_result.get("confidence", 0) > 0.8:
                return {
                    "success": True,
                    "result": edge_result,
                    "processing_mode": "edge_ai_primary",
                    "server_processing_skipped": True
                }
            else:
                # 서버 처리 필요
                return {
                    "success": True,
                    "result": edge_result,
                    "processing_mode": "hybrid_edge_server",
                    "requires_server_refinement": True
                }
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _handle_offline_optimized(self, request_data: Dict) -> Dict:
        """오프라인 최적화 처리"""
        return await self.pwa_service.handle_offline_request(request_data)
    
    def get_mobile_capabilities(self) -> Dict:
        """모바일 기능 정보"""
        return {
            "edge_ai_available": self.edge_ai.model_loaded,
            "offline_processing": True,
            "pwa_features": self.pwa_service.offline_capabilities,
            "configuration": self.mobile_config,
            "statistics": self.edge_ai.get_offline_stats()
        }

# 싱글톤 인스턴스들
edge_ai_processor = EdgeAIProcessor()
pwa_service = PWAService()
mobile_optimization_service = MobileOptimizationService() 