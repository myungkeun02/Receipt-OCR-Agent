#!/usr/bin/env python3
"""
🔄 Redis 연동 테스트 스크립트

이 스크립트는 Redis 연결 및 캐싱 기능을 테스트합니다.
"""

import logging
import time
import json
from datetime import datetime

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_redis_connection():
    """Redis 기본 연결 테스트"""
    print("\n🔍 Redis 연결 테스트 시작...")
    
    try:
        import redis
        
        # Redis 클라이언트 생성
        r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        
        # 연결 테스트
        r.ping()
        print("✅ Redis 연결 성공!")
        
        # 기본 정보 조회
        info = r.info()
        print(f"📊 Redis 버전: {info.get('redis_version', 'unknown')}")
        print(f"💾 메모리 사용량: {info.get('used_memory_human', 'N/A')}")
        print(f"👥 연결된 클라이언트: {info.get('connected_clients', 0)}")
        print(f"⏱️  가동 시간: {info.get('uptime_in_seconds', 0)} 초")
        
        return r
        
    except ImportError:
        print("❌ Redis 라이브러리가 설치되지 않았습니다.")
        print("💡 설치 명령: pip install redis")
        return None
    except redis.ConnectionError:
        print("❌ Redis 서버에 연결할 수 없습니다.")
        print("💡 Redis 서버를 시작하세요: redis-server")
        return None
    except Exception as e:
        print(f"❌ Redis 연결 오류: {e}")
        return None

def test_basic_operations(redis_client):
    """기본 Redis 연산 테스트"""
    print("\n🧪 기본 Redis 연산 테스트...")
    
    try:
        # SET/GET 테스트
        test_key = "test:receipt_ai"
        test_value = {
            "message": "Hello Redis!",
            "timestamp": datetime.now().isoformat(),
            "version": "1.0.0"
        }
        
        # 데이터 저장
        redis_client.setex(test_key, 60, json.dumps(test_value))
        print(f"✅ 데이터 저장 성공: {test_key}")
        
        # 데이터 조회
        retrieved = redis_client.get(test_key)
        if retrieved:
            parsed_data = json.loads(retrieved)
            print(f"✅ 데이터 조회 성공: {parsed_data['message']}")
        
        # TTL 확인
        ttl = redis_client.ttl(test_key)
        print(f"⏰ TTL: {ttl} 초")
        
        # 데이터 삭제
        redis_client.delete(test_key)
        print("🗑️  테스트 데이터 삭제 완료")
        
        return True
        
    except Exception as e:
        print(f"❌ 기본 연산 테스트 실패: {e}")
        return False

def test_cache_service_integration():
    """캐시 서비스 통합 테스트"""
    print("\n🔧 캐시 서비스 통합 테스트...")
    
    try:
        from services.cache_service import cache_service, redis_cache_manager
        
        # 캐시 서비스 상태 확인
        cache_stats = cache_service.get_cache_stats()
        print(f"📊 캐시 통계: {cache_stats}")
        
        # Redis 정보 확인
        redis_info = cache_service.get_redis_info()
        if redis_info.get('connected'):
            print("✅ 캐시 서비스 Redis 연결 정상")
        else:
            print("❌ 캐시 서비스 Redis 연결 실패")
            return False
        
        # 테스트 데이터 캐싱
        test_ocr_data = {
            "merchant_name": "테스트 카페",
            "total_price": 5000,
            "transaction_date": "2025-01-15"
        }
        
        test_hash = "test_image_hash_12345"
        
        # OCR 결과 캐싱 테스트
        cache_success = redis_cache_manager.cache_ocr_result(test_hash, test_ocr_data, expire_hours=1)
        if cache_success:
            print("✅ OCR 결과 캐싱 성공")
        else:
            print("❌ OCR 결과 캐싱 실패")
        
        # 캐시된 데이터 조회 테스트
        cached_result = redis_cache_manager.get_cached_ocr_result(test_hash)
        if cached_result and cached_result.get('merchant_name') == '테스트 카페':
            print("✅ OCR 결과 조회 성공")
        else:
            print("❌ OCR 결과 조회 실패")
        
        # LLM 결과 캐싱 테스트
        test_llm_data = {
            "account_category": "복리후생비",
            "description": "커피",
            "confidence": 0.95
        }
        
        test_prompt_hash = "test_prompt_hash_67890"
        
        llm_cache_success = redis_cache_manager.cache_llm_response(test_prompt_hash, test_llm_data, expire_minutes=60)
        if llm_cache_success:
            print("✅ LLM 결과 캐싱 성공")
        
        # 캐시 통계 확인
        redis_stats = redis_cache_manager.get_cache_statistics()
        print(f"📈 네임스페이스별 키 개수: OCR={redis_stats.get('ocr_keys', 0)}, LLM={redis_stats.get('llm_keys', 0)}")
        
        # 테스트 데이터 정리
        redis_cache_manager.clear_namespace('ocr')
        redis_cache_manager.clear_namespace('llm')
        print("🧹 테스트 데이터 정리 완료")
        
        return True
        
    except Exception as e:
        print(f"❌ 캐시 서비스 통합 테스트 실패: {e}")
        return False

def test_performance_impact():
    """성능 영향 테스트"""
    print("\n⚡ 성능 영향 테스트...")
    
    try:
        from services.cache_service import redis_cache_manager
        import hashlib
        
        # 대량 데이터 캐싱 테스트
        start_time = time.time()
        
        test_data_list = []
        for i in range(100):
            test_data = {
                "id": i,
                "merchant": f"테스트 상점 {i}",
                "amount": 1000 + i * 10,
                "timestamp": datetime.now().isoformat()
            }
            
            hash_key = hashlib.md5(f"test_batch_{i}".encode()).hexdigest()
            redis_cache_manager.cache_ocr_result(hash_key, test_data, expire_hours=1)
            test_data_list.append(hash_key)
        
        cache_time = time.time() - start_time
        print(f"📊 100개 항목 캐싱 시간: {cache_time:.3f} 초")
        
        # 대량 데이터 조회 테스트
        start_time = time.time()
        
        hit_count = 0
        for hash_key in test_data_list:
            cached_result = redis_cache_manager.get_cached_ocr_result(hash_key)
            if cached_result:
                hit_count += 1
        
        retrieval_time = time.time() - start_time
        print(f"📊 100개 항목 조회 시간: {retrieval_time:.3f} 초")
        print(f"🎯 캐시 적중률: {hit_count}/100 ({hit_count}%)")
        
        # 테스트 데이터 정리
        redis_cache_manager.clear_namespace('ocr')
        
        # 성능 요약
        if cache_time < 1.0 and retrieval_time < 0.1:
            print("✅ 캐시 성능: 우수")
        elif cache_time < 2.0 and retrieval_time < 0.5:
            print("✅ 캐시 성능: 양호")
        else:
            print("⚠️  캐시 성능: 개선 필요")
        
        return True
        
    except Exception as e:
        print(f"❌ 성능 테스트 실패: {e}")
        return False

def display_redis_recommendations():
    """Redis 최적화 권장사항"""
    print("\n💡 Redis 최적화 권장사항:")
    print("   📈 성능 향상:")
    print("      - 메모리 충분히 할당 (최소 512MB)")
    print("      - 영구 저장 설정 (RDB + AOF)")
    print("      - 적절한 만료 시간 설정")
    print("\n   🔧 설정 최적화:")
    print("      - maxmemory-policy: allkeys-lru")
    print("      - save 설정으로 주기적 백업")
    print("      - 모니터링 도구 연동")
    print("\n   🔒 보안 강화:")
    print("      - 패스워드 설정 (requirepass)")
    print("      - 네트워크 접근 제한")
    print("      - SSL/TLS 사용 고려")

def main():
    """메인 테스트 함수"""
    print("🚀 Redis 연동 테스트 시작")
    print("=" * 50)
    
    # 1. Redis 연결 테스트
    redis_client = test_redis_connection()
    if not redis_client:
        print("\n❌ Redis 연결 실패로 테스트를 중단합니다.")
        return False
    
    # 2. 기본 연산 테스트
    if not test_basic_operations(redis_client):
        print("\n❌ 기본 연산 테스트 실패")
        return False
    
    # 3. 캐시 서비스 통합 테스트
    if not test_cache_service_integration():
        print("\n❌ 캐시 서비스 통합 테스트 실패")
        return False
    
    # 4. 성능 테스트
    if not test_performance_impact():
        print("\n❌ 성능 테스트 실패")
        return False
    
    # 5. 권장사항 표시
    display_redis_recommendations()
    
    print("\n" + "=" * 50)
    print("🎉 모든 Redis 테스트 완료!")
    print("\n📊 테스트 결과 요약:")
    print("   ✅ Redis 연결: 성공")
    print("   ✅ 기본 연산: 성공")
    print("   ✅ 캐시 서비스: 성공")
    print("   ✅ 성능 테스트: 성공")
    print("\n💡 이제 다음 명령으로 API 서버를 시작하세요:")
    print("   python app.py")
    print("\n🌐 Redis 캐시 상태 확인:")
    print("   GET http://localhost:5001/receipt/cache/status")
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️  사용자에 의해 중단되었습니다.")
        exit(0)
    except Exception as e:
        print(f"\n❌ 테스트 중 오류 발생: {e}")
        exit(1) 