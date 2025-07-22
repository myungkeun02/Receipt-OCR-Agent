import logging
import json
import hashlib
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import pickle
from config.settings import Config

class CacheService:
    """고성능 캐싱 및 최적화 서비스"""
    
    def __init__(self):
        self.config = Config
        self.memory_cache = {}  # 메모리 캐시 (개발용)
        self.cache_stats = {
            'hits': 0,
            'misses': 0,
            'sets': 0
        }
        
        # Redis 연결 (실제 환경에서 사용)
        self.redis_client = None
        try:
            import redis
            self.redis_client = redis.Redis(
                host='localhost', 
                port=6379, 
                db=1,
                decode_responses=True,
                socket_timeout=5,  # 타임아웃 증가
                socket_connect_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            # 연결 테스트
            self.redis_client.ping()
            
            # Redis 정보 조회
            redis_info = self.redis_client.info()
            redis_version = redis_info.get('redis_version', 'unknown')
            
            logging.info(f"✅ Redis connected successfully - Version: {redis_version}")
            logging.info(f"📊 Redis Memory: {redis_info.get('used_memory_human', 'N/A')}")
            
        except ImportError:
            logging.warning("❌ Redis library not installed. Install with: pip install redis")
        except redis.ConnectionError as e:
            logging.warning(f"❌ Redis connection failed: {e}")
            logging.warning("💡 Make sure Redis server is running: redis-server")
        except Exception as e:
            logging.warning(f"❌ Redis initialization error: {e}")
            logging.warning("🔄 Falling back to memory cache")
    
    def get(self, key: str) -> Optional[Any]:
        """캐시에서 데이터 조회"""
        try:
            # Redis 우선 시도
            if self.redis_client:
                value = self.redis_client.get(key)
                if value:
                    self.cache_stats['hits'] += 1
                    return json.loads(value)
            
            # 메모리 캐시 시도
            if key in self.memory_cache:
                cache_entry = self.memory_cache[key]
                if cache_entry['expires'] > datetime.now():
                    self.cache_stats['hits'] += 1
                    return cache_entry['data']
                else:
                    # 만료된 캐시 제거
                    del self.memory_cache[key]
            
            self.cache_stats['misses'] += 1
            return None
            
        except Exception as e:
            logging.error(f"Cache get failed for key {key}: {e}")
            self.cache_stats['misses'] += 1
            return None
    
    def set(self, key: str, value: Any, expire_minutes: int = 60) -> bool:
        """캐시에 데이터 저장"""
        try:
            self.cache_stats['sets'] += 1
            
            # Redis에 저장
            if self.redis_client:
                json_value = json.dumps(value, default=str)
                return self.redis_client.setex(key, expire_minutes * 60, json_value)
            
            # 메모리 캐시에 저장
            expires = datetime.now() + timedelta(minutes=expire_minutes)
            self.memory_cache[key] = {
                'data': value,
                'expires': expires
            }
            
            # 메모리 캐시 크기 제한 (최대 1000개)
            if len(self.memory_cache) > 1000:
                self._cleanup_memory_cache()
            
            return True
            
        except Exception as e:
            logging.error(f"Cache set failed for key {key}: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """캐시에서 데이터 삭제"""
        try:
            deleted = False
            
            # Redis에서 삭제
            if self.redis_client:
                deleted = bool(self.redis_client.delete(key))
            
            # 메모리 캐시에서 삭제
            if key in self.memory_cache:
                del self.memory_cache[key]
                deleted = True
            
            return deleted
            
        except Exception as e:
            logging.error(f"Cache delete failed for key {key}: {e}")
            return False
    
    def _cleanup_memory_cache(self):
        """만료된 메모리 캐시 정리"""
        now = datetime.now()
        expired_keys = [
            key for key, entry in self.memory_cache.items()
            if entry['expires'] <= now
        ]
        
        for key in expired_keys:
            del self.memory_cache[key]
        
        # 여전히 크기가 크면 오래된 것부터 삭제
        if len(self.memory_cache) > 1000:
            items = list(self.memory_cache.items())
            items.sort(key=lambda x: x[1]['expires'])
            
            # 가장 오래된 200개 삭제
            for key, _ in items[:200]:
                del self.memory_cache[key]
    
    def cache_query_result(self, query: str, params: tuple, result: Any, expire_minutes: int = 30) -> bool:
        """DB 쿼리 결과 캐싱"""
        cache_key = self._generate_query_cache_key(query, params)
        return self.set(cache_key, result, expire_minutes)
    
    def get_cached_query_result(self, query: str, params: tuple) -> Optional[Any]:
        """캐시된 DB 쿼리 결과 조회"""
        cache_key = self._generate_query_cache_key(query, params)
        return self.get(cache_key)
    
    def _generate_query_cache_key(self, query: str, params: tuple) -> str:
        """쿼리 캐시 키 생성"""
        query_normalized = ' '.join(query.split())  # 공백 정규화
        cache_input = f"{query_normalized}:{str(params)}"
        return f"query:{hashlib.md5(cache_input.encode()).hexdigest()}"
    
    def cache_ocr_result(self, image_hash: str, ocr_result: Dict, expire_hours: int = 24) -> bool:
        """OCR 결과 캐싱 (같은 이미지 재처리 방지)"""
        cache_key = f"ocr:{image_hash}"
        return self.set(cache_key, ocr_result, expire_hours * 60)
    
    def get_cached_ocr_result(self, image_hash: str) -> Optional[Dict]:
        """캐시된 OCR 결과 조회"""
        cache_key = f"ocr:{image_hash}"
        return self.get(cache_key)
    
    def cache_llm_result(self, prompt_hash: str, llm_result: Dict, expire_minutes: int = 120) -> bool:
        """LLM 결과 캐싱 (동일 프롬프트 재사용)"""
        cache_key = f"llm:{prompt_hash}"
        return self.set(cache_key, llm_result, expire_minutes)
    
    def get_cached_llm_result(self, prompt_hash: str) -> Optional[Dict]:
        """캐시된 LLM 결과 조회"""
        cache_key = f"llm:{prompt_hash}"
        return self.get(cache_key)
    
    def cache_account_categories(self, categories: List[Dict], expire_hours: int = 12) -> bool:
        """계정과목 목록 캐싱 (자주 조회되는 데이터)"""
        cache_key = "account_categories:active"
        return self.set(cache_key, categories, expire_hours * 60)
    
    def get_cached_account_categories(self) -> Optional[List[Dict]]:
        """캐시된 계정과목 목록 조회"""
        cache_key = "account_categories:active"
        return self.get(cache_key)
    
    def cache_user_patterns(self, user_id: str, patterns: Dict, expire_hours: int = 6) -> bool:
        """사용자 패턴 캐싱"""
        cache_key = f"user_patterns:{user_id}"
        return self.set(cache_key, patterns, expire_hours * 60)
    
    def get_cached_user_patterns(self, user_id: str) -> Optional[Dict]:
        """캐시된 사용자 패턴 조회"""
        cache_key = f"user_patterns:{user_id}"
        return self.get(cache_key)
    
    def invalidate_pattern(self, pattern: str) -> int:
        """패턴 기반 캐시 무효화"""
        try:
            invalidated_count = 0
            
            if self.redis_client:
                # Redis에서 패턴 매칭 키 찾기
                keys = self.redis_client.keys(pattern)
                if keys:
                    invalidated_count = self.redis_client.delete(*keys)
            
            # 메모리 캐시에서 패턴 매칭 키 찾기
            matching_keys = [
                key for key in self.memory_cache.keys()
                if pattern.replace('*', '') in key
            ]
            
            for key in matching_keys:
                del self.memory_cache[key]
                invalidated_count += 1
            
            logging.info(f"Invalidated {invalidated_count} cache keys matching pattern: {pattern}")
            return invalidated_count
            
        except Exception as e:
            logging.error(f"Cache invalidation failed for pattern {pattern}: {e}")
            return 0
    
    def get_cache_stats(self) -> Dict:
        """캐시 통계 조회"""
        total_requests = self.cache_stats['hits'] + self.cache_stats['misses']
        hit_rate = (self.cache_stats['hits'] / total_requests * 100) if total_requests > 0 else 0
        
        stats = {
            'hits': self.cache_stats['hits'],
            'misses': self.cache_stats['misses'],
            'sets': self.cache_stats['sets'],
            'hit_rate': round(hit_rate, 2),
            'memory_cache_size': len(self.memory_cache)
        }
        
        # Redis 통계 추가
        if self.redis_client:
            try:
                redis_info = self.redis_client.info('memory')
                stats['redis_memory_usage'] = redis_info.get('used_memory_human', 'N/A')
                stats['redis_connected'] = True
            except:
                stats['redis_connected'] = False
        else:
            stats['redis_connected'] = False
        
        return stats
    
    def warm_up_cache(self) -> Dict:
        """캐시 워밍업 (자주 사용되는 데이터 미리 로딩)"""
        try:
            from services.db_service import db_service
            
            warmup_results = {
                'account_categories': False,
                'recent_patterns': False,
                'system_stats': False
            }
            
            # 1. 계정과목 목록 캐싱
            try:
                categories = db_service.get_account_categories()
                if categories:
                    self.cache_account_categories(categories)
                    warmup_results['account_categories'] = True
            except Exception as e:
                logging.error(f"Failed to warm up account categories: {e}")
            
            # 2. 최근 패턴 캐싱
            try:
                # 최근 7일간 자주 사용된 패턴들을 미리 캐싱
                # (실제 구현에서는 복잡한 쿼리 결과를 캐싱)
                recent_patterns = {"cached": True, "timestamp": datetime.now().isoformat()}
                self.set("recent_patterns:weekly", recent_patterns, 360)  # 6시간
                warmup_results['recent_patterns'] = True
            except Exception as e:
                logging.error(f"Failed to warm up recent patterns: {e}")
            
            # 3. 시스템 통계 캐싱
            try:
                system_stats = {"warmed_up": True, "timestamp": datetime.now().isoformat()}
                self.set("system_stats:dashboard", system_stats, 60)  # 1시간
                warmup_results['system_stats'] = True
            except Exception as e:
                logging.error(f"Failed to warm up system stats: {e}")
            
            logging.info(f"Cache warmup completed: {warmup_results}")
            return warmup_results
            
        except Exception as e:
            logging.error(f"Cache warmup failed: {e}")
            return {"error": str(e)}
    
    def generate_image_hash(self, image_data: bytes) -> str:
        """이미지 해시 생성 (OCR 캐싱용)"""
        return hashlib.sha256(image_data).hexdigest()
    
    def generate_prompt_hash(self, prompt: str) -> str:
        """프롬프트 해시 생성 (LLM 캐싱용)"""
        return hashlib.md5(prompt.encode()).hexdigest()
    
    def redis_set_with_expiry(self, key: str, value: Any, expire_seconds: int = 3600) -> bool:
        """Redis 전용 만료시간 설정"""
        if not self.redis_client:
            return False
        
        try:
            json_value = json.dumps(value, default=str, ensure_ascii=False)
            return self.redis_client.setex(key, expire_seconds, json_value)
        except Exception as e:
            logging.error(f"Redis setex failed for key {key}: {e}")
            return False
    
    def redis_get_with_ttl(self, key: str) -> Dict:
        """Redis에서 값과 TTL 함께 조회"""
        if not self.redis_client:
            return {"value": None, "ttl": -1, "exists": False}
        
        try:
            # 파이프라인을 사용해 원자적 연산
            pipe = self.redis_client.pipeline()
            pipe.get(key)
            pipe.ttl(key)
            results = pipe.execute()
            
            value_str, ttl = results
            
            if value_str is None:
                return {"value": None, "ttl": -1, "exists": False}
            
            value = json.loads(value_str)
            return {"value": value, "ttl": ttl, "exists": True}
            
        except Exception as e:
            logging.error(f"Redis get with TTL failed for key {key}: {e}")
            return {"value": None, "ttl": -1, "exists": False}
    
    def redis_mget(self, keys: List[str]) -> Dict[str, Any]:
        """Redis에서 여러 키 한번에 조회"""
        if not self.redis_client or not keys:
            return {}
        
        try:
            values = self.redis_client.mget(keys)
            result = {}
            
            for key, value_str in zip(keys, values):
                if value_str is not None:
                    try:
                        result[key] = json.loads(value_str)
                    except json.JSONDecodeError:
                        logging.warning(f"Failed to decode JSON for key {key}")
                        result[key] = None
                else:
                    result[key] = None
            
            return result
            
        except Exception as e:
            logging.error(f"Redis mget failed: {e}")
            return {}
    
    def redis_delete_pattern(self, pattern: str) -> int:
        """Redis에서 패턴 매칭하는 키들 삭제"""
        if not self.redis_client:
            return 0
        
        try:
            keys = self.redis_client.keys(pattern)
            if keys:
                return self.redis_client.delete(*keys)
            return 0
            
        except Exception as e:
            logging.error(f"Redis pattern delete failed: {e}")
            return 0
    
    def get_redis_info(self) -> Dict:
        """Redis 서버 정보 조회"""
        if not self.redis_client:
            return {"connected": False, "error": "Redis client not available"}
        
        try:
            info = self.redis_client.info()
            
            return {
                "connected": True,
                "redis_version": info.get('redis_version'),
                "used_memory": info.get('used_memory_human'),
                "connected_clients": info.get('connected_clients'),
                "total_commands_processed": info.get('total_commands_processed'),
                "uptime_in_seconds": info.get('uptime_in_seconds'),
                "keyspace": self.redis_client.info('keyspace')
            }
            
        except Exception as e:
            return {"connected": False, "error": str(e)}

# 성능 최적화 도우미 클래스
class PerformanceOptimizer:
    """성능 최적화 도구"""
    
    def __init__(self, cache_service: CacheService):
        self.cache = cache_service
    
    def cached_db_query(self, func, *args, expire_minutes: int = 30, **kwargs):
        """DB 쿼리 결과를 자동으로 캐싱하는 데코레이터"""
        # 캐시 키 생성
        cache_key = f"db:{func.__name__}:{hashlib.md5(str(args + tuple(kwargs.items())).encode()).hexdigest()}"
        
        # 캐시에서 먼저 조회
        cached_result = self.cache.get(cache_key)
        if cached_result is not None:
            return cached_result
        
        # 캐시 미스 시 실제 쿼리 실행
        result = func(*args, **kwargs)
        
        # 결과 캐싱
        self.cache.set(cache_key, result, expire_minutes)
        
        return result
    
    def batch_invalidate_user_cache(self, user_id: str):
        """사용자 관련 모든 캐시 무효화"""
        patterns = [
            f"user_patterns:{user_id}",
            f"user_stats:{user_id}*",
            f"user_recommendations:{user_id}*"
        ]
        
        for pattern in patterns:
            self.cache.invalidate_pattern(pattern)

class RedisCacheManager:
    """Redis 전용 고성능 캐시 매니저"""
    
    def __init__(self, cache_service: CacheService):
        self.cache_service = cache_service
        self.redis_client = cache_service.redis_client
        
        # 캐시 네임스페이스 정의
        self.namespaces = {
            'ocr': 'receipt:ocr:',
            'llm': 'receipt:llm:',
            'user': 'receipt:user:',
            'analytics': 'receipt:analytics:',
            'categories': 'receipt:categories:',
            'session': 'receipt:session:'
        }
    
    def cache_ocr_result(self, image_hash: str, ocr_result: Dict, expire_hours: int = 24) -> bool:
        """OCR 결과를 Redis에 캐싱 (24시간)"""
        if not self.redis_client:
            return False
        
        try:
            key = f"{self.namespaces['ocr']}{image_hash}"
            expire_seconds = expire_hours * 3600
            
            cache_data = {
                'ocr_result': ocr_result,
                'cached_at': datetime.now().isoformat(),
                'expires_at': (datetime.now() + timedelta(hours=expire_hours)).isoformat()
            }
            
            success = self.cache_service.redis_set_with_expiry(key, cache_data, expire_seconds)
            
            if success:
                logging.info(f"✅ OCR result cached: {key} (expires in {expire_hours}h)")
            
            return success
            
        except Exception as e:
            logging.error(f"❌ OCR caching failed: {e}")
            return False
    
    def get_cached_ocr_result(self, image_hash: str) -> Optional[Dict]:
        """캐시된 OCR 결과 조회"""
        if not self.redis_client:
            return None
        
        try:
            key = f"{self.namespaces['ocr']}{image_hash}"
            cache_data = self.cache_service.redis_get_with_ttl(key)
            
            if cache_data['exists']:
                logging.info(f"🎯 OCR cache hit: {key} (TTL: {cache_data['ttl']}s)")
                return cache_data['value']['ocr_result']
            
            return None
            
        except Exception as e:
            logging.error(f"❌ OCR cache retrieval failed: {e}")
            return None
    
    def cache_llm_response(self, prompt_hash: str, llm_response: Dict, expire_minutes: int = 120) -> bool:
        """LLM 응답을 Redis에 캐싱 (2시간)"""
        if not self.redis_client:
            return False
        
        try:
            key = f"{self.namespaces['llm']}{prompt_hash}"
            expire_seconds = expire_minutes * 60
            
            cache_data = {
                'llm_response': llm_response,
                'model_used': 'gpt-3.5-turbo',  # 실제 사용된 모델
                'cached_at': datetime.now().isoformat(),
                'prompt_hash': prompt_hash
            }
            
            success = self.cache_service.redis_set_with_expiry(key, cache_data, expire_seconds)
            
            if success:
                logging.info(f"✅ LLM response cached: {key} (expires in {expire_minutes}m)")
            
            return success
            
        except Exception as e:
            logging.error(f"❌ LLM caching failed: {e}")
            return False
    
    def get_cached_llm_response(self, prompt_hash: str) -> Optional[Dict]:
        """캐시된 LLM 응답 조회"""
        if not self.redis_client:
            return None
        
        try:
            key = f"{self.namespaces['llm']}{prompt_hash}"
            cache_data = self.cache_service.redis_get_with_ttl(key)
            
            if cache_data['exists']:
                logging.info(f"🎯 LLM cache hit: {key} (TTL: {cache_data['ttl']}s)")
                return cache_data['value']['llm_response']
            
            return None
            
        except Exception as e:
            logging.error(f"❌ LLM cache retrieval failed: {e}")
            return None
    
    def cache_user_session(self, session_id: str, user_data: Dict, expire_minutes: int = 60) -> bool:
        """사용자 세션 정보 캐싱"""
        if not self.redis_client:
            return False
        
        try:
            key = f"{self.namespaces['session']}{session_id}"
            expire_seconds = expire_minutes * 60
            
            session_data = {
                'user_data': user_data,
                'last_activity': datetime.now().isoformat(),
                'session_id': session_id
            }
            
            return self.cache_service.redis_set_with_expiry(key, session_data, expire_seconds)
            
        except Exception as e:
            logging.error(f"❌ Session caching failed: {e}")
            return False
    
    def get_cached_user_session(self, session_id: str) -> Optional[Dict]:
        """캐시된 사용자 세션 조회"""
        if not self.redis_client:
            return None
        
        try:
            key = f"{self.namespaces['session']}{session_id}"
            cache_data = self.cache_service.redis_get_with_ttl(key)
            
            if cache_data['exists']:
                return cache_data['value']['user_data']
            
            return None
            
        except Exception as e:
            logging.error(f"❌ Session retrieval failed: {e}")
            return None
    
    def cache_analytics_data(self, cache_key: str, analytics_result: Dict, expire_minutes: int = 30) -> bool:
        """분석 결과 캐싱"""
        if not self.redis_client:
            return False
        
        try:
            key = f"{self.namespaces['analytics']}{cache_key}"
            expire_seconds = expire_minutes * 60
            
            cache_data = {
                'analytics_result': analytics_result,
                'generated_at': datetime.now().isoformat(),
                'cache_key': cache_key
            }
            
            return self.cache_service.redis_set_with_expiry(key, cache_data, expire_seconds)
            
        except Exception as e:
            logging.error(f"❌ Analytics caching failed: {e}")
            return False
    
    def get_cached_analytics_data(self, cache_key: str) -> Optional[Dict]:
        """캐시된 분석 결과 조회"""
        if not self.redis_client:
            return None
        
        try:
            key = f"{self.namespaces['analytics']}{cache_key}"
            cache_data = self.cache_service.redis_get_with_ttl(key)
            
            if cache_data['exists']:
                return cache_data['value']['analytics_result']
            
            return None
            
        except Exception as e:
            logging.error(f"❌ Analytics retrieval failed: {e}")
            return None
    
    def batch_cache_account_categories(self, categories: List[Dict]) -> bool:
        """계정과목 목록을 Redis에 배치 캐싱"""
        if not self.redis_client or not categories:
            return False
        
        try:
            # 전체 목록 캐싱
            key = f"{self.namespaces['categories']}all"
            expire_seconds = 12 * 3600  # 12시간
            
            cache_data = {
                'categories': categories,
                'total_count': len(categories),
                'cached_at': datetime.now().isoformat()
            }
            
            success = self.cache_service.redis_set_with_expiry(key, cache_data, expire_seconds)
            
            # 개별 카테고리도 캐싱 (빠른 조회용)
            for category in categories:
                cat_key = f"{self.namespaces['categories']}by_name:{category.get('name', '')}"
                self.cache_service.redis_set_with_expiry(cat_key, category, expire_seconds)
            
            if success:
                logging.info(f"✅ Account categories cached: {len(categories)} items")
            
            return success
            
        except Exception as e:
            logging.error(f"❌ Categories caching failed: {e}")
            return False
    
    def get_cached_account_categories(self) -> Optional[List[Dict]]:
        """캐시된 계정과목 목록 조회"""
        if not self.redis_client:
            return None
        
        try:
            key = f"{self.namespaces['categories']}all"
            cache_data = self.cache_service.redis_get_with_ttl(key)
            
            if cache_data['exists']:
                logging.info(f"🎯 Categories cache hit (TTL: {cache_data['ttl']}s)")
                return cache_data['value']['categories']
            
            return None
            
        except Exception as e:
            logging.error(f"❌ Categories retrieval failed: {e}")
            return None
    
    def clear_namespace(self, namespace: str) -> int:
        """특정 네임스페이스의 모든 키 삭제"""
        if namespace not in self.namespaces:
            logging.warning(f"Unknown namespace: {namespace}")
            return 0
        
        pattern = f"{self.namespaces[namespace]}*"
        deleted_count = self.cache_service.redis_delete_pattern(pattern)
        
        if deleted_count > 0:
            logging.info(f"🗑️  Cleared {deleted_count} keys from namespace '{namespace}'")
        
        return deleted_count
    
    def get_cache_statistics(self) -> Dict:
        """캐시 통계 조회"""
        if not self.redis_client:
            return {"error": "Redis not connected"}
        
        try:
            stats = {}
            
            # 네임스페이스별 키 개수
            for ns_name, ns_prefix in self.namespaces.items():
                pattern = f"{ns_prefix}*"
                keys = self.redis_client.keys(pattern)
                stats[f"{ns_name}_keys"] = len(keys)
            
            # Redis 전체 정보
            redis_info = self.cache_service.get_redis_info()
            stats.update(redis_info)
            
            return stats
            
        except Exception as e:
            return {"error": str(e)}

# 싱글톤 인스턴스
cache_service = CacheService()
performance_optimizer = PerformanceOptimizer(cache_service)
redis_cache_manager = RedisCacheManager(cache_service) 