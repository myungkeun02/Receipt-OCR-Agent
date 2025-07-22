import mysql.connector
import logging
from typing import Optional, List, Dict, Tuple
from config.settings import Config

class DatabaseService:
    """데이터베이스 연결 및 쿼리를 담당하는 서비스 클래스"""
    
    def __init__(self):
        self.config = Config
    
    def get_connection(self):
        """MySQL 데이터베이스 연결 생성"""
        try:
            connection = mysql.connector.connect(
                host=self.config.DB_HOST,
                port=self.config.DB_PORT,
                user=self.config.DB_USER,
                password=self.config.DB_PASSWORD,
                database=self.config.DB_NAME,
                charset='utf8mb4',
                autocommit=True
            )
            return connection
        except mysql.connector.Error as e:
            logging.error(f"Database connection failed: {e}")
            return None
    
    def get_account_categories(self) -> List[Dict]:
        """
        활성화된 모든 계정과목 조회 (Redis 캐싱 적용)
        
        Returns:
            List[Dict]: 계정과목 목록
        """
        # Redis 캐시에서 먼저 확인
        try:
            from services.cache_service import redis_cache_manager
            
            cached_categories = redis_cache_manager.get_cached_account_categories()
            if cached_categories:
                logging.info(f"🎯 Categories cache hit - using cached {len(cached_categories)} categories")
                return cached_categories
        except Exception as cache_error:
            logging.warning(f"Categories cache check failed: {cache_error}")
        
        db = self.get_connection()
        if not db:
            return []
        
        try:
            logging.info("📊 Fetching fresh categories from database...")
            
            cursor = db.cursor(dictionary=True)
            cursor.execute("""
                SELECT id, name, code, description 
                FROM account_categories 
                WHERE isActive = 1
                ORDER BY name
            """)
            
            categories = cursor.fetchall()
            logging.info(f"Retrieved {len(categories)} account categories from DB")
            
            # 결과를 Redis에 캐싱 (12시간)
            try:
                redis_cache_manager.batch_cache_account_categories(categories)
                logging.info(f"✅ Categories cached: {len(categories)} items")
            except Exception as cache_error:
                logging.warning(f"Categories caching failed: {cache_error}")
            
            return categories
            
        except mysql.connector.Error as e:
            logging.error(f"Failed to fetch account categories: {e}")
            return []
        finally:
            if db:
                db.close()
    
    def find_exact_match(self, ocr_data: str) -> Optional[Tuple[str, str, int]]:
        """
        정확한 ocrData 매칭으로 계정과목과 설명 찾기
        
        Args:
            ocr_data: OCR로 추출한 사용처
            
        Returns:
            Tuple[account_category, description, confidence] or None
        """
        db = self.get_connection()
        if not db:
            return None
        
        try:
            cursor = db.cursor(dictionary=True)
            cursor.execute("""
                SELECT accountCategory, description, COUNT(*) as frequency
                FROM expense_items 
                WHERE ocrData = %s
                GROUP BY accountCategory, description
                ORDER BY frequency DESC
                LIMIT 1
            """, (ocr_data,))
            
            result = cursor.fetchone()
            if result and result['frequency'] >= 2:
                logging.info(f"Found exact match: {result}")
                return result['accountCategory'], result['description'], 95
            
            return None
            
        except mysql.connector.Error as e:
            logging.error(f"Database exact match query error: {e}")
            return None
        finally:
            if db:
                db.close()
    
    def find_keyword_matches(self, keywords: List[str], price_range: str) -> Optional[Tuple[str, str, int]]:
        """
        키워드 기반 매칭으로 계정과목과 설명 찾기
        
        Args:
            keywords: 추출된 키워드 목록
            price_range: 가격 구간
            
        Returns:
            Tuple[account_category, description, confidence] or None
        """
        db = self.get_connection()
        if not db:
            return None
        
        try:
            cursor = db.cursor(dictionary=True)
            
            for keyword in keywords:
                # ocrData와 description에서 키워드 검색
                cursor.execute("""
                    SELECT accountCategory, description, COUNT(*) as frequency
                    FROM expense_items 
                    WHERE (ocrData LIKE %s OR description LIKE %s)
                    GROUP BY accountCategory, description
                    ORDER BY frequency DESC
                    LIMIT 1
                """, (f"%{keyword}%", f"%{keyword}%"))
                
                result = cursor.fetchone()
                if result and result['frequency'] >= 3:
                    logging.info(f"Found keyword match for '{keyword}': {result}")
                    return result['accountCategory'], result['description'], 75
            
            return None
            
        except mysql.connector.Error as e:
            logging.error(f"Database keyword match query error: {e}")
            return None
        finally:
            if db:
                db.close()
    
    def get_price_pattern_analysis(self, keywords: List[str], price_range: str) -> Optional[Tuple[str, str, int]]:
        """
        가격대별 패턴 분석
        
        Args:
            keywords: 키워드 목록
            price_range: 가격 구간
            
        Returns:
            Tuple[account_category, description, confidence] or None
        """
        db = self.get_connection()
        if not db:
            return None
        
        try:
            cursor = db.cursor(dictionary=True)
            
            # 키워드와 가격대를 조합한 패턴 검색
            for keyword in keywords:
                cursor.execute("""
                    SELECT ei.accountCategory, ei.description, COUNT(*) as frequency,
                           AVG(CASE WHEN ei.amount BETWEEN %s AND %s THEN 1 ELSE 0 END) as price_match_ratio
                    FROM expense_items ei
                    WHERE (ei.ocrData LIKE %s OR ei.description LIKE %s)
                    GROUP BY ei.accountCategory, ei.description
                    HAVING frequency >= 2 AND price_match_ratio > 0.3
                    ORDER BY frequency DESC, price_match_ratio DESC
                    LIMIT 1
                """, self._get_price_range_bounds(price_range) + (f"%{keyword}%", f"%{keyword}%"))
                
                result = cursor.fetchone()
                if result:
                    confidence = min(85, 60 + (result['frequency'] * 5))
                    logging.info(f"Found price pattern for '{keyword}': {result}")
                    return result['accountCategory'], result['description'], confidence
            
            return None
            
        except mysql.connector.Error as e:
            logging.error(f"Database price pattern query error: {e}")
            return None
        finally:
            if db:
                db.close()
    
    def _get_price_range_bounds(self, price_range: str) -> Tuple[int, int]:
        """가격 구간에 따른 최소/최대 금액 반환"""
        price_bounds = {
            "very_low": (0, 5000),
            "low": (5001, 10000),
            "medium": (10001, 50000),
            "high": (50001, 200000),
            "very_high": (200001, 999999999),
            "unknown": (0, 999999999)
        }
        return price_bounds.get(price_range, (0, 999999999))
    
    def save_expense_item(self, expense_data: Dict) -> bool:
        """
        expense_items 테이블에 새로운 데이터 저장
        
        Args:
            expense_data: 저장할 지출 데이터
            
        Returns:
            bool: 저장 성공 여부
        """
        db = self.get_connection()
        if not db:
            return False
        
        try:
            cursor = db.cursor()
            
            # 필수 필드 확인
            required_fields = ['ocrData', 'accountCategory', 'description', 'amount', 'usageDate']
            if not all(field in expense_data for field in required_fields):
                logging.error(f"Missing required fields in expense_data: {expense_data}")
                return False
            
            cursor.execute("""
                INSERT INTO expense_items 
                (ocrData, accountCategory, description, amount, usageDate, createdAt, updatedAt)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
            """, (
                expense_data['ocrData'],
                expense_data['accountCategory'],
                expense_data['description'],
                expense_data['amount'],
                expense_data['usageDate']
            ))
            
            logging.info(f"Expense item saved successfully: {expense_data['ocrData']}")
            return True
            
        except mysql.connector.Error as e:
            logging.error(f"Failed to save expense item: {e}")
            return False
        finally:
            if db:
                db.close()
    
    def get_statistics(self) -> Dict:
        """
        데이터베이스 통계 정보 조회
        
        Returns:
            Dict: 통계 정보
        """
        db = self.get_connection()
        if not db:
            return {}
        
        try:
            cursor = db.cursor(dictionary=True)
            
            # 전체 지출 항목 수
            cursor.execute("SELECT COUNT(*) as total_items FROM expense_items")
            total_items = cursor.fetchone()['total_items']
            
            # 계정과목별 분포
            cursor.execute("""
                SELECT accountCategory, COUNT(*) as count
                FROM expense_items
                GROUP BY accountCategory
                ORDER BY count DESC
                LIMIT 10
            """)
            category_distribution = cursor.fetchall()
            
            # 최근 30일 데이터 수
            cursor.execute("""
                SELECT COUNT(*) as recent_items
                FROM expense_items
                WHERE createdAt >= DATE_SUB(NOW(), INTERVAL 30 DAY)
            """)
            recent_items = cursor.fetchone()['recent_items']
            
            return {
                'total_items': total_items,
                'recent_items': recent_items,
                'category_distribution': category_distribution
            }
            
        except mysql.connector.Error as e:
            logging.error(f"Failed to get statistics: {e}")
            return {}
        finally:
            if db:
                db.close()

# 싱글톤 인스턴스 생성
db_service = DatabaseService() 