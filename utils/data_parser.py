import re
from datetime import datetime
import logging

def parse_total_price(price_str):
    """
    가격 문자열을 정수로 파싱
    예: "69,445원", "₩69445", "12345" → 69445
    """
    if not price_str:
        return None
        
    # 숫자와 소수점을 제외한 모든 문자 제거
    numeric_str = re.sub(r'[^\d.]', '', str(price_str))
    
    try:
        # float으로 변환 후 int로 변환 (소수점 처리)
        return int(float(numeric_str))
    except (ValueError, TypeError):
        logging.warning(f"Failed to parse price: {price_str}")
        return None

def parse_transaction_date(date_str):
    """
    다양한 한국어 날짜 형식을 YYYY-MM-DD로 파싱
    예: "2025년 07월 09일", "25.07.09", "2025-07-09"
    """
    if not date_str:
        return None
        
    date_str = str(date_str).strip()
    
    # 1. YYYY년 MM월 DD일 형식
    match = re.match(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일', date_str)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    # 2. YY.MM.DD 또는 YYYY.MM.DD 형식
    match = re.match(r'(\d{2}|\d{4})\.(\d{1,2})\.(\d{1,2})', date_str)
    if match:
        year, month, day = match.groups()
        if len(year) == 2:
            year = f"20{year}"  # 21세기 가정
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    
    # 3. 일반적인 형식들 시도
    date_formats = [
        "%Y-%m-%d", "%y-%m-%d", 
        "%Y.%m.%d", "%y.%m.%d", 
        "%Y/%m/%d", "%y/%m/%d"
    ]
    
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    logging.warning(f"Failed to parse date: {date_str}")
    return None

def extract_keywords_from_ocr(ocr_data):
    """
    OCR 데이터에서 의미있는 키워드 추출
    브랜드명, 업종별 키워드를 식별
    """
    if not ocr_data:
        return []
        
    ocr_data = str(ocr_data)
    keywords = []
    
    # 주요 브랜드/업종 키워드 매핑
    keyword_patterns = {
        'cafe': ['스타벅스', '투썸', '카페', '커피', '빈스', '이디야', '할리스', '메가커피'],
        'mart': ['이마트', '롯데마트', '홈플러스', '마트', '슈퍼마켓', '슈퍼'],
        'convenience': ['GS25', 'CU', '세븐일레븐', '미니스톱', '편의점'],
        'gas_station': ['주유소', 'GS칼텍스', 'SK에너지', 'S-Oil', 'LPG', '셀프주유'],
        'restaurant': ['식당', '치킨', '피자', '맥도날드', 'KFC', '버거킹', '롯데리아'],
        'pharmacy': ['약국', '온누리약국', '경희약국', '메디팜'],
        'hospital': ['병원', '의원', '한의원', '치과', '안과', '내과'],
        'transport': ['택시', '버스', '지하철', '톨게이트', '주차', '교통카드'],
        'bank': ['은행', '농협', '신한', '우리', 'KB국민', 'ATM'],
        'education': ['학원', '교육', '도서관', '서점', '문구점']
    }
    
    # 카테고리별 키워드 매칭
    for category, patterns in keyword_patterns.items():
        for pattern in patterns:
            if pattern in ocr_data:
                keywords.append(pattern)
    
    # 일반적인 단어 분리 (2글자 이상)
    words = re.findall(r'[가-힣a-zA-Z]{2,}', ocr_data)
    keywords.extend(words)
    
    # 중복 제거 및 정렬
    return sorted(list(set(keywords)), key=len, reverse=True)

def get_price_range(price):
    """
    가격을 구간별로 분류
    분석 시 가격대별 패턴 파악에 활용
    """
    if not price or price <= 0:
        return "unknown"
    elif price <= 5000:
        return "very_low"    # 5천원 이하
    elif price <= 10000:
        return "low"         # 1만원 이하
    elif price <= 50000:
        return "medium"      # 5만원 이하
    elif price <= 200000:
        return "high"        # 20만원 이하
    else:
        return "very_high"   # 20만원 초과

def clean_text(text):
    """
    텍스트 정리 (공백, 특수문자 등 제거)
    """
    if not text:
        return ""
    
    # 불필요한 공백 제거
    cleaned = re.sub(r'\s+', ' ', str(text)).strip()
    
    # 특수문자 일부 제거 (필요시 확장)
    cleaned = re.sub(r'[^\w\s가-힣.-]', '', cleaned)
    
    return cleaned

def extract_numbers(text):
    """
    텍스트에서 모든 숫자 추출
    """
    if not text:
        return []
    
    numbers = re.findall(r'\d+', str(text))
    return [int(num) for num in numbers if num.isdigit()] 