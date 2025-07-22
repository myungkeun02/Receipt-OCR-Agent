import requests
import uuid
import base64
import time
import json
import logging
from typing import Dict, Optional
from config.settings import Config

class OCRService:
    """네이버 CLOVA OCR API를 담당하는 서비스 클래스"""
    
    def __init__(self):
        self.config = Config
        self.api_key = self.config.CLOVA_OCR_API_KEY
        self.endpoint = self.config.CLOVA_OCR_ENDPOINT
    
    def extract_text_from_image(self, image_data: bytes) -> Dict:
        """
        이미지에서 텍스트 추출 (Redis 캐싱 적용)
        
        Args:
            image_data: 이미지 바이너리 데이터
            
        Returns:
            Dict: OCR 결과 JSON
        """
        # Redis 캐싱 적용
        try:
            from services.cache_service import redis_cache_manager
            import hashlib
            
            # 이미지 해시 생성
            image_hash = hashlib.sha256(image_data).hexdigest()
            
            # 캐시에서 먼저 확인
            cached_result = redis_cache_manager.get_cached_ocr_result(image_hash)
            if cached_result:
                logging.info(f"🎯 OCR cache hit - using cached result for {image_hash[:12]}...")
                return cached_result
        except Exception as cache_error:
            logging.warning(f"Cache check failed: {cache_error}")
        
        if not self.api_key or not self.endpoint:
            logging.warning("CLOVA OCR API key or endpoint not set. Using mock data.")
            mock_result = self._get_mock_data()
            
            # Mock 결과도 캐싱 (1시간)
            try:
                redis_cache_manager.cache_ocr_result(image_hash, mock_result, expire_hours=1)
            except:
                pass
            
            return mock_result
        
        try:
            logging.info(f"📸 Processing new image with OCR: {image_hash[:12]}...")
            
            payload = self._build_request_payload(image_data)
            headers = self._build_request_headers()
            
            logging.info(f"Sending OCR request to: {self.endpoint}")
            response = requests.post(self.endpoint, headers=headers, json=payload)
            response.raise_for_status()
            
            ocr_result = response.json()
            logging.info("Successfully received OCR response")
            
            # 응답 데이터 검증
            if not self._validate_response(ocr_result):
                raise ValueError("Invalid OCR response format")
            
            # 성공한 결과를 Redis에 캐싱 (24시간)
            try:
                redis_cache_manager.cache_ocr_result(image_hash, ocr_result, expire_hours=24)
                logging.info(f"✅ OCR result cached for {image_hash[:12]}...")
            except Exception as cache_error:
                logging.warning(f"OCR result caching failed: {cache_error}")
            
            return ocr_result
            
        except requests.exceptions.RequestException as e:
            logging.error(f"OCR API request failed: {e}")
            raise
        except (json.JSONDecodeError, ValueError) as e:
            logging.error(f"OCR response parsing failed: {e}")
            raise
    
    def _build_request_payload(self, image_data: bytes) -> Dict:
        """OCR API 요청 페이로드 생성"""
        return {
            "version": "V2",
            "requestId": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "lang": "ko",
            "images": [
                {
                    "format": "jpeg",  # 필요시 동적으로 감지 가능
                    "name": "receipt",
                    "data": base64.b64encode(image_data).decode('utf-8')
                }
            ]
        }
    
    def _build_request_headers(self) -> Dict:
        """OCR API 요청 헤더 생성"""
        return {
            "X-OCR-SECRET": self.api_key,
            "Content-Type": "application/json"
        }
    
    def _validate_response(self, response: Dict) -> bool:
        """OCR 응답 데이터 검증"""
        try:
            # 기본 구조 확인
            if 'images' not in response:
                return False
            
            images = response.get('images', [])
            if not images or not isinstance(images, list):
                return False
            
            # 첫 번째 이미지에 fields가 있는지 확인
            first_image = images[0]
            if 'fields' not in first_image:
                logging.warning("No 'fields' found in OCR response")
                # fields가 없어도 처리 가능하도록 유연하게 처리
            
            return True
            
        except (KeyError, IndexError, TypeError) as e:
            logging.error(f"OCR response validation failed: {e}")
            return False
    
    def _get_mock_data(self) -> Dict:
        """API 키가 없을 때 사용할 목업 데이터"""
        return {
            "images": [
                {
                    "fields": [
                        {"inferText": "스타벅스 강남점"},
                        {"inferText": "2025-07-09"},
                        {"inferText": "4,500원"},
                        {"inferText": "승인번호: 12345678"}
                    ],
                    "receipt": {
                        "result": {
                            "storeInfo": {
                                "name": {"text": "스타벅스 강남점"}
                            },
                            "paymentInfo": {
                                "date": {"text": "2025-07-09"},
                                "totalPrice": {"text": "4,500원"}
                            },
                            "cardInfo": {
                                "approvalNo": {"text": "12345678"}
                            }
                        }
                    }
                }
            ]
        }
    
    def extract_text_blocks(self, ocr_result: Dict) -> str:
        """
        OCR 결과에서 모든 텍스트를 하나의 문자열로 추출
        
        Args:
            ocr_result: OCR API 응답 데이터
            
        Returns:
            str: 추출된 텍스트 블록
        """
        try:
            all_text_fields = ocr_result.get('images', [{}])[0].get('fields', [])
            text_blocks = []
            
            for field in all_text_fields:
                text = field.get('inferText', '').strip()
                if text:  # 빈 텍스트 제외
                    text_blocks.append(text)
            
            combined_text = "\\n".join(text_blocks)
            logging.info(f"Extracted {len(text_blocks)} text blocks from OCR")
            
            return combined_text
            
        except (KeyError, IndexError, TypeError) as e:
            logging.error(f"Failed to extract text blocks: {e}")
            return ""
    
    def get_structured_receipt_data(self, ocr_result: Dict) -> Dict:
        """
        OCR 결과에서 구조화된 영수증 데이터 추출 (CLOVA의 영수증 전용 기능)
        
        Args:
            ocr_result: OCR API 응답 데이터
            
        Returns:
            Dict: 구조화된 영수증 데이터
        """
        try:
            receipt_info = ocr_result.get('images', [{}])[0].get('receipt', {}).get('result', {})
            
            # 구조화된 데이터 추출
            structured_data = {
                'store_name': self._safe_extract_text(receipt_info, ['storeInfo', 'name']),
                'payment_date': self._safe_extract_text(receipt_info, ['paymentInfo', 'date']),
                'total_price': self._safe_extract_text(receipt_info, ['paymentInfo', 'totalPrice']),
                'approval_no': self._safe_extract_text(receipt_info, ['cardInfo', 'approvalNo']),
                'card_company': self._safe_extract_text(receipt_info, ['cardInfo', 'company']),
                'card_number': self._safe_extract_text(receipt_info, ['cardInfo', 'number'])
            }
            
            logging.info(f"Extracted structured receipt data: {structured_data}")
            return structured_data
            
        except (KeyError, IndexError, TypeError) as e:
            logging.error(f"Failed to extract structured receipt data: {e}")
            return {}
    
    def _safe_extract_text(self, data: Dict, keys: list) -> Optional[str]:
        """
        중첩된 딕셔너리에서 안전하게 텍스트 추출
        
        Args:
            data: 원본 데이터
            keys: 접근할 키 경로
            
        Returns:
            str or None: 추출된 텍스트
        """
        try:
            current = data
            for key in keys:
                current = current.get(key, {})
            
            return current.get('text') if isinstance(current, dict) else None
            
        except (AttributeError, TypeError):
            return None
    
    def get_confidence_info(self, ocr_result: Dict) -> Dict:
        """
        OCR 신뢰도 정보 추출
        
        Args:
            ocr_result: OCR API 응답 데이터
            
        Returns:
            Dict: 신뢰도 정보
        """
        try:
            fields = ocr_result.get('images', [{}])[0].get('fields', [])
            
            confidences = []
            for field in fields:
                confidence = field.get('inferConfidence', 0)
                if confidence > 0:
                    confidences.append(confidence)
            
            if confidences:
                avg_confidence = sum(confidences) / len(confidences)
                min_confidence = min(confidences)
                max_confidence = max(confidences)
                
                return {
                    'average_confidence': round(avg_confidence, 2),
                    'min_confidence': round(min_confidence, 2),
                    'max_confidence': round(max_confidence, 2),
                    'total_fields': len(confidences)
                }
            
            return {'message': 'No confidence data available'}
            
        except (KeyError, IndexError, TypeError) as e:
            logging.error(f"Failed to extract confidence info: {e}")
            return {}

# 싱글톤 인스턴스 생성
ocr_service = OCRService() 