import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Application Configuration ---
class Config:
    # Flask Configuration
    FLASK_DEBUG = True
    FLASK_HOST = '0.0.0.0'
    FLASK_PORT = 5001
    
    # API Keys
    CLOVA_OCR_API_KEY = os.getenv("CLOVA_OCR_API_KEY")
    CLOVA_OCR_ENDPOINT = os.getenv("CLOVA_OCR_ENDPOINT")
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    
    # Database Configuration
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', 3306))
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    DB_NAME = os.getenv('DB_NAME', 'receipt_ai')
    
    # AI Configuration
    CONFIDENCE_THRESHOLD = 80  # DB 데이터 사용을 위한 최소 신뢰도
    LLM_MODEL = "gpt-3.5-turbo"
    LLM_TEMPERATURE = 0.1
    
    @classmethod
    def validate_config(cls):
        """필수 설정값들이 제대로 로드되었는지 확인"""
        missing_configs = []
        
        if not cls.CLOVA_OCR_API_KEY:
            missing_configs.append("CLOVA_OCR_API_KEY")
        if not cls.CLOVA_OCR_ENDPOINT:
            missing_configs.append("CLOVA_OCR_ENDPOINT")
            
        if missing_configs:
            logging.warning(f"Missing configurations: {missing_configs}")
            logging.warning("Some features may not work properly")
        
        return len(missing_configs) == 0

# --- Logging Configuration ---
def setup_logging():
    """로깅 설정 초기화"""
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

# --- URL Sanitization ---
def clean_endpoint_url(endpoint_url):
    """엔드포인트 URL 정리 (http -> https 변환 등)"""
    if not endpoint_url:
        return endpoint_url
        
    # 공백 및 줄바꿈 제거
    cleaned_url = "".join(endpoint_url.split())
    
    # http를 https로 변경 (보안 강화)
    if cleaned_url.startswith("http://"):
        cleaned_url = cleaned_url.replace("http://", "https://", 1)
        logging.warning(f"HTTP endpoint converted to HTTPS: {cleaned_url}")
    
    return cleaned_url

# 설정 적용
Config.CLOVA_OCR_ENDPOINT = clean_endpoint_url(Config.CLOVA_OCR_ENDPOINT)
setup_logging() 