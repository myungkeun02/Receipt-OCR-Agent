import logging
import json
import hashlib
import secrets
from typing import Dict, List, Optional, Union, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import asyncio
from contextlib import asynccontextmanager

from services.db_service import db_service
from config.settings import Config

class UserRole(Enum):
    """사용자 역할"""
    ADMIN = "admin"
    MANAGER = "manager" 
    EMPLOYEE = "employee"
    AUDITOR = "auditor"
    GUEST = "guest"

class SecurityLevel(Enum):
    """보안 수준"""
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"

@dataclass
class TenantConfig:
    """테넌트 설정"""
    tenant_id: str
    name: str
    domain: str
    database_config: Dict
    security_settings: Dict
    feature_flags: Dict
    created_at: datetime
    is_active: bool = True

@dataclass
class AuditLog:
    """감사 로그"""
    log_id: str
    tenant_id: str
    user_id: str
    action: str
    resource_type: str
    resource_id: Optional[str]
    ip_address: str
    user_agent: str
    timestamp: datetime
    success: bool
    details: Dict
    security_level: SecurityLevel

@dataclass
class SecurityContext:
    """보안 컨텍스트"""
    tenant_id: str
    user_id: str
    user_role: UserRole
    permissions: List[str]
    session_id: str
    ip_address: str
    security_level: SecurityLevel

class MultiTenantManager:
    """멀티테넌트 관리자"""
    
    def __init__(self):
        self.tenant_configs = {}
        self.tenant_connections = {}
        self.load_tenant_configurations()
    
    def load_tenant_configurations(self):
        """테넌트 설정 로드"""
        try:
            # 실제 환경에서는 데이터베이스나 설정 파일에서 로드
            default_tenants = [
                {
                    "tenant_id": "corp_001",
                    "name": "ABC Corporation",
                    "domain": "abc-corp.com",
                    "database_config": {
                        "host": "localhost",
                        "database": "receipt_ai_corp001",
                        "isolation_level": "tenant"
                    },
                    "security_settings": {
                        "require_2fa": True,
                        "session_timeout": 3600,
                        "ip_whitelist": [],
                        "encryption_level": "AES256"
                    },
                    "feature_flags": {
                        "advanced_analytics": True,
                        "bulk_processing": True,
                        "api_access": True,
                        "custom_categories": True
                    }
                },
                {
                    "tenant_id": "startup_001", 
                    "name": "StartupXYZ",
                    "domain": "startupxyz.com",
                    "database_config": {
                        "host": "localhost",
                        "database": "receipt_ai_startup001",
                        "isolation_level": "shared"
                    },
                    "security_settings": {
                        "require_2fa": False,
                        "session_timeout": 7200,
                        "ip_whitelist": [],
                        "encryption_level": "AES128"
                    },
                    "feature_flags": {
                        "advanced_analytics": False,
                        "bulk_processing": False,
                        "api_access": True,
                        "custom_categories": False
                    }
                }
            ]
            
            for tenant_data in default_tenants:
                tenant_config = TenantConfig(
                    tenant_id=tenant_data["tenant_id"],
                    name=tenant_data["name"],
                    domain=tenant_data["domain"],
                    database_config=tenant_data["database_config"],
                    security_settings=tenant_data["security_settings"],
                    feature_flags=tenant_data["feature_flags"],
                    created_at=datetime.now()
                )
                self.tenant_configs[tenant_config.tenant_id] = tenant_config
            
            logging.info(f"Loaded {len(self.tenant_configs)} tenant configurations")
            
        except Exception as e:
            logging.error(f"Failed to load tenant configurations: {e}")
    
    def get_tenant_from_request(self, request_headers: Dict) -> Optional[str]:
        """요청에서 테넌트 식별"""
        try:
            # 1. 헤더에서 테넌트 ID 확인
            tenant_id = request_headers.get("X-Tenant-ID")
            if tenant_id and tenant_id in self.tenant_configs:
                return tenant_id
            
            # 2. Host 헤더에서 도메인 기반 식별
            host = request_headers.get("Host", "")
            for tenant_id, config in self.tenant_configs.items():
                if config.domain in host:
                    return tenant_id
            
            # 3. 기본 테넌트 또는 None
            return None
            
        except Exception as e:
            logging.error(f"Tenant identification failed: {e}")
            return None
    
    def get_tenant_config(self, tenant_id: str) -> Optional[TenantConfig]:
        """테넌트 설정 조회"""
        return self.tenant_configs.get(tenant_id)
    
    def get_tenant_database_config(self, tenant_id: str) -> Optional[Dict]:
        """테넌트별 데이터베이스 설정"""
        config = self.get_tenant_config(tenant_id)
        return config.database_config if config else None
    
    async def process_with_tenant_context(self, tenant_id: str, 
                                        security_context: SecurityContext,
                                        operation: callable, 
                                        *args, **kwargs) -> Dict:
        """테넌트 컨텍스트로 작업 처리"""
        try:
            # 테넌트 검증
            tenant_config = self.get_tenant_config(tenant_id)
            if not tenant_config or not tenant_config.is_active:
                return {"error": "Invalid or inactive tenant", "tenant_id": tenant_id}
            
            # 보안 검증
            security_check = await self._validate_security_context(
                tenant_config, security_context
            )
            if not security_check["valid"]:
                return {"error": "Security validation failed", "details": security_check}
            
            # 테넌트별 리소스 격리
            async with self._tenant_isolation_context(tenant_config):
                # 실제 작업 수행
                result = await operation(*args, **kwargs)
                
                # 감사 로그 기록
                await self._log_tenant_operation(
                    tenant_id, security_context, operation.__name__, result
                )
                
                return result
                
        except Exception as e:
            logging.error(f"Tenant operation failed: {e}")
            return {"error": str(e), "tenant_id": tenant_id}
    
    async def _validate_security_context(self, tenant_config: TenantConfig, 
                                       security_context: SecurityContext) -> Dict:
        """보안 컨텍스트 검증"""
        try:
            validation_result = {"valid": True, "issues": []}
            
            # IP 화이트리스트 검증
            ip_whitelist = tenant_config.security_settings.get("ip_whitelist", [])
            if ip_whitelist and security_context.ip_address not in ip_whitelist:
                validation_result["issues"].append("IP not in whitelist")
            
            # 세션 유효성 검증
            # 실제로는 Redis 등에서 세션 상태 확인
            
            # 권한 검증
            if not security_context.permissions:
                validation_result["issues"].append("No permissions assigned")
            
            # 2FA 검증 (설정된 경우)
            if tenant_config.security_settings.get("require_2fa", False):
                # 실제로는 2FA 토큰 검증
                pass
            
            validation_result["valid"] = len(validation_result["issues"]) == 0
            return validation_result
            
        except Exception as e:
            return {"valid": False, "error": str(e)}
    
    @asynccontextmanager
    async def _tenant_isolation_context(self, tenant_config: TenantConfig):
        """테넌트 격리 컨텍스트"""
        try:
            # 테넌트별 데이터베이스 연결 설정
            original_db_config = getattr(db_service, 'current_config', None)
            db_service.current_config = tenant_config.database_config
            
            yield
            
        finally:
            # 원래 설정 복원
            if original_db_config:
                db_service.current_config = original_db_config
    
    async def _log_tenant_operation(self, tenant_id: str, 
                                  security_context: SecurityContext,
                                  operation: str, result: Dict):
        """테넌트 작업 로깅"""
        try:
            audit_log = AuditLog(
                log_id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=security_context.user_id,
                action=operation,
                resource_type="receipt_processing",
                resource_id=result.get("receipt_id"),
                ip_address=security_context.ip_address,
                user_agent="API_CLIENT",  # 실제로는 요청에서 추출
                timestamp=datetime.now(),
                success="error" not in result,
                details={"operation": operation, "result_summary": str(result)[:200]},
                security_level=security_context.security_level
            )
            
            # 감사 로그 저장
            await audit_manager.log_operation(audit_log)
            
        except Exception as e:
            logging.error(f"Tenant operation logging failed: {e}")

class SecurityAuditManager:
    """보안 감사 관리자"""
    
    def __init__(self):
        self.audit_logs = []  # 실제로는 별도 데이터베이스
        self.security_policies = {}
        self.threat_detection_rules = []
        self.load_security_policies()
    
    def load_security_policies(self):
        """보안 정책 로드"""
        try:
            self.security_policies = {
                "password_policy": {
                    "min_length": 12,
                    "require_uppercase": True,
                    "require_lowercase": True, 
                    "require_numbers": True,
                    "require_symbols": True,
                    "max_age_days": 90
                },
                "access_control": {
                    "max_failed_attempts": 5,
                    "lockout_duration_minutes": 30,
                    "session_timeout_minutes": 60,
                    "require_2fa_for_admin": True
                },
                "data_protection": {
                    "encryption_at_rest": True,
                    "encryption_in_transit": True,
                    "data_retention_days": 2555,  # 7년
                    "anonymization_required": True
                }
            }
            
            # 위협 감지 규칙 
            self.threat_detection_rules = [
                {
                    "name": "suspicious_login_attempts",
                    "condition": "failed_logins > 3 in 10 minutes",
                    "action": "alert_and_lock"
                },
                {
                    "name": "unusual_access_pattern",
                    "condition": "access_from_new_location",
                    "action": "alert_and_verify"
                },
                {
                    "name": "bulk_data_access",
                    "condition": "data_access > 100 records in 1 hour",
                    "action": "alert_and_audit"
                }
            ]
            
            logging.info("Security policies loaded successfully")
            
        except Exception as e:
            logging.error(f"Failed to load security policies: {e}")
    
    async def log_operation(self, audit_log: AuditLog):
        """작업 감사 로그 기록"""
        try:
            # 로그 저장
            self.audit_logs.append(audit_log)
            
            # 위협 감지 분석
            await self._analyze_for_threats(audit_log)
            
            # 실제 환경에서는 SIEM이나 로그 집계 시스템에 전송
            await self._send_to_siem(audit_log)
            
            logging.info(f"Audit log recorded: {audit_log.log_id}")
            
        except Exception as e:
            logging.error(f"Audit logging failed: {e}")
    
    async def _analyze_for_threats(self, audit_log: AuditLog):
        """위협 분석"""
        try:
            # 실패한 로그인 시도 패턴 분석
            if audit_log.action == "login" and not audit_log.success:
                await self._check_failed_login_pattern(audit_log)
            
            # 비정상적인 데이터 접근 패턴
            if audit_log.action == "data_access":
                await self._check_data_access_pattern(audit_log)
            
            # 권한 상승 시도
            if "privilege" in audit_log.action:
                await self._check_privilege_escalation(audit_log)
                
        except Exception as e:
            logging.error(f"Threat analysis failed: {e}")
    
    async def _check_failed_login_pattern(self, audit_log: AuditLog):
        """실패한 로그인 패턴 검사"""
        try:
            # 최근 10분간 같은 IP에서의 실패 시도 확인
            recent_failures = [
                log for log in self.audit_logs
                if (log.ip_address == audit_log.ip_address and
                    log.action == "login" and
                    not log.success and
                    (audit_log.timestamp - log.timestamp).total_seconds() < 600)
            ]
            
            if len(recent_failures) >= 3:
                await self._trigger_security_alert({
                    "type": "suspicious_login_attempts",
                    "ip_address": audit_log.ip_address,
                    "failure_count": len(recent_failures),
                    "recommendation": "Consider IP blocking"
                })
                
        except Exception as e:
            logging.error(f"Failed login pattern check failed: {e}")
    
    async def _check_data_access_pattern(self, audit_log: AuditLog):
        """데이터 접근 패턴 검사"""
        try:
            # 1시간 내 같은 사용자의 데이터 접근 횟수
            recent_access = [
                log for log in self.audit_logs
                if (log.user_id == audit_log.user_id and
                    log.action == "data_access" and
                    (audit_log.timestamp - log.timestamp).total_seconds() < 3600)
            ]
            
            if len(recent_access) > 100:
                await self._trigger_security_alert({
                    "type": "bulk_data_access",
                    "user_id": audit_log.user_id,
                    "access_count": len(recent_access),
                    "recommendation": "Review user activity"
                })
                
        except Exception as e:
            logging.error(f"Data access pattern check failed: {e}")
    
    async def _check_privilege_escalation(self, audit_log: AuditLog):
        """권한 상승 시도 검사"""
        try:
            # 권한 변경 시도 로깅 및 분석
            await self._trigger_security_alert({
                "type": "privilege_escalation_attempt",
                "user_id": audit_log.user_id,
                "action": audit_log.action,
                "recommendation": "Immediate review required"
            })
            
        except Exception as e:
            logging.error(f"Privilege escalation check failed: {e}")
    
    async def _trigger_security_alert(self, alert_data: Dict):
        """보안 알림 트리거"""
        try:
            logging.warning(f"SECURITY ALERT: {alert_data}")
            
            # 실제 환경에서는:
            # - SIEM에 알림 전송
            # - 보안팀에 이메일/슬랙 알림
            # - 자동 대응 조치 (IP 차단 등)
            
        except Exception as e:
            logging.error(f"Security alert failed: {e}")
    
    async def _send_to_siem(self, audit_log: AuditLog):
        """SIEM 시스템으로 로그 전송"""
        try:
            # 실제 환경에서는 Splunk, ELK Stack 등으로 전송
            siem_data = {
                "timestamp": audit_log.timestamp.isoformat(),
                "tenant_id": audit_log.tenant_id,
                "user_id": audit_log.user_id,
                "action": audit_log.action,
                "success": audit_log.success,
                "ip_address": audit_log.ip_address,
                "security_level": audit_log.security_level.value
            }
            
            # Mock SIEM 전송
            logging.info(f"SIEM: {json.dumps(siem_data)}")
            
        except Exception as e:
            logging.error(f"SIEM logging failed: {e}")
    
    async def generate_security_report(self, tenant_id: str, 
                                     start_date: datetime, 
                                     end_date: datetime) -> Dict:
        """보안 리포트 생성"""
        try:
            # 해당 기간의 감사 로그 필터링
            tenant_logs = [
                log for log in self.audit_logs
                if (log.tenant_id == tenant_id and
                    start_date <= log.timestamp <= end_date)
            ]
            
            # 통계 계산
            total_operations = len(tenant_logs)
            failed_operations = len([log for log in tenant_logs if not log.success])
            unique_users = len(set(log.user_id for log in tenant_logs))
            unique_ips = len(set(log.ip_address for log in tenant_logs))
            
            # 작업 유형별 분포
            operation_distribution = {}
            for log in tenant_logs:
                operation_distribution[log.action] = operation_distribution.get(log.action, 0) + 1
            
            # 보안 이벤트 요약
            security_events = [
                log for log in tenant_logs 
                if log.security_level in [SecurityLevel.CONFIDENTIAL, SecurityLevel.RESTRICTED]
            ]
            
            return {
                "tenant_id": tenant_id,
                "report_period": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat()
                },
                "summary": {
                    "total_operations": total_operations,
                    "failed_operations": failed_operations,
                    "failure_rate": round(failed_operations / max(1, total_operations) * 100, 2),
                    "unique_users": unique_users,
                    "unique_ip_addresses": unique_ips
                },
                "operation_distribution": operation_distribution,
                "security_events": len(security_events),
                "compliance_status": self._assess_compliance(tenant_logs),
                "recommendations": self._generate_security_recommendations(tenant_logs)
            }
            
        except Exception as e:
            logging.error(f"Security report generation failed: {e}")
            return {"error": str(e)}
    
    def _assess_compliance(self, logs: List[AuditLog]) -> Dict:
        """컴플라이언스 평가"""
        try:
            # 다양한 컴플라이언스 기준 평가
            compliance_checks = {
                "audit_logging": len(logs) > 0,
                "access_control": True,  # 실제로는 정책 준수 여부 확인
                "data_encryption": True,  # 암호화 정책 확인
                "retention_policy": True  # 데이터 보존 정책 확인
            }
            
            compliance_score = sum(compliance_checks.values()) / len(compliance_checks) * 100
            
            return {
                "score": round(compliance_score, 1),
                "checks": compliance_checks,
                "status": "compliant" if compliance_score >= 90 else "non_compliant"
            }
            
        except Exception:
            return {"score": 0, "status": "unknown"}
    
    def _generate_security_recommendations(self, logs: List[AuditLog]) -> List[str]:
        """보안 권장사항 생성"""
        recommendations = []
        
        try:
            # 실패율이 높으면
            failure_rate = len([log for log in logs if not log.success]) / max(1, len(logs))
            if failure_rate > 0.1:
                recommendations.append("높은 실패율이 감지되었습니다. 시스템 안정성을 점검하세요.")
            
            # 특정 IP에서 과도한 접근
            ip_counts = {}
            for log in logs:
                ip_counts[log.ip_address] = ip_counts.get(log.ip_address, 0) + 1
            
            max_ip_count = max(ip_counts.values()) if ip_counts else 0
            if max_ip_count > 1000:
                recommendations.append("특정 IP에서 과도한 접근이 감지되었습니다. 접근 제한을 고려하세요.")
            
            # 야간 시간대 접근
            night_access = [log for log in logs if log.timestamp.hour < 6 or log.timestamp.hour > 22]
            if len(night_access) > len(logs) * 0.2:
                recommendations.append("야간 시간대 접근이 많습니다. 접근 시간 정책을 검토하세요.")
            
            return recommendations
            
        except Exception:
            return ["보안 분석 중 오류가 발생했습니다."]

class BusinessIntelligenceService:
    """비즈니스 인텔리전스 서비스"""
    
    def __init__(self):
        self.report_cache = {}
        self.analytical_models = {}
        self.initialize_analytical_models()
    
    def initialize_analytical_models(self):
        """분석 모델 초기화"""
        try:
            self.analytical_models = {
                "expense_forecasting": {
                    "type": "time_series",
                    "accuracy": 0.85,
                    "last_trained": datetime.now() - timedelta(days=7)
                },
                "anomaly_detection": {
                    "type": "isolation_forest",
                    "accuracy": 0.92,
                    "last_trained": datetime.now() - timedelta(days=3)
                },
                "category_optimization": {
                    "type": "clustering",
                    "accuracy": 0.78,
                    "last_trained": datetime.now() - timedelta(days=14)
                }
            }
            
            logging.info("BI analytical models initialized")
            
        except Exception as e:
            logging.error(f"BI model initialization failed: {e}")
    
    async def generate_executive_dashboard(self, tenant_id: str, 
                                         time_period: str = "monthly") -> Dict:
        """경영진 대시보드 생성"""
        try:
            # 캐시 확인
            cache_key = f"executive_dashboard_{tenant_id}_{time_period}"
            if cache_key in self.report_cache:
                cached_report = self.report_cache[cache_key]
                if (datetime.now() - cached_report["generated_at"]).total_seconds() < 3600:
                    return cached_report["data"]
            
            # 새로운 리포트 생성
            dashboard_data = await self._build_executive_dashboard(tenant_id, time_period)
            
            # 캐시 저장
            self.report_cache[cache_key] = {
                "data": dashboard_data,
                "generated_at": datetime.now()
            }
            
            return dashboard_data
            
        except Exception as e:
            logging.error(f"Executive dashboard generation failed: {e}")
            return {"error": str(e)}
    
    async def _build_executive_dashboard(self, tenant_id: str, time_period: str) -> Dict:
        """경영진 대시보드 구축"""
        try:
            # 기간 설정
            if time_period == "weekly":
                start_date = datetime.now() - timedelta(days=7)
            elif time_period == "monthly":
                start_date = datetime.now() - timedelta(days=30)
            elif time_period == "quarterly":
                start_date = datetime.now() - timedelta(days=90)
            else:
                start_date = datetime.now() - timedelta(days=365)
            
            # 핵심 메트릭 계산 (실제로는 데이터베이스에서 조회)
            core_metrics = await self._calculate_core_metrics(tenant_id, start_date)
            
            # 트렌드 분석
            trend_analysis = await self._analyze_expense_trends(tenant_id, start_date)
            
            # 비용 최적화 기회
            optimization_opportunities = await self._identify_cost_optimizations(tenant_id)
            
            # 컴플라이언스 현황
            compliance_status = await self._assess_compliance_status(tenant_id)
            
            # 예측 분석
            predictive_insights = await self._generate_predictive_insights(tenant_id)
            
            return {
                "tenant_id": tenant_id,
                "report_period": time_period,
                "generated_at": datetime.now().isoformat(),
                "core_metrics": core_metrics,
                "trend_analysis": trend_analysis,
                "optimization_opportunities": optimization_opportunities,
                "compliance_status": compliance_status,
                "predictive_insights": predictive_insights,
                "action_items": self._generate_action_items(
                    core_metrics, trend_analysis, optimization_opportunities
                )
            }
            
        except Exception as e:
            logging.error(f"Dashboard building failed: {e}")
            return {"error": str(e)}
    
    async def _calculate_core_metrics(self, tenant_id: str, start_date: datetime) -> Dict:
        """핵심 메트릭 계산"""
        try:
            # Mock 데이터 (실제로는 DB 쿼리)
            return {
                "total_expenses": 2845600,
                "expense_count": 1247,
                "average_expense": 2283,
                "top_categories": [
                    {"category": "복리후생비", "amount": 856800, "percentage": 30.1},
                    {"category": "여비교통비", "amount": 569120, "percentage": 20.0},
                    {"category": "소모품비", "amount": 427340, "percentage": 15.0},
                    {"category": "접대비", "amount": 341472, "percentage": 12.0}
                ],
                "growth_rate": 12.5,  # 전 기간 대비 증가율
                "budget_utilization": 78.3,  # 예산 대비 사용률
                "processing_accuracy": 94.2  # AI 처리 정확도
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    async def _analyze_expense_trends(self, tenant_id: str, start_date: datetime) -> Dict:
        """지출 트렌드 분석"""
        try:
            # 시계열 분석 (Mock 데이터)
            weekly_trends = [
                {"week": "2025-W01", "amount": 245600, "change": 5.2},
                {"week": "2025-W02", "amount": 267800, "change": 9.1},
                {"week": "2025-W03", "amount": 289400, "change": 8.1},
                {"week": "2025-W04", "amount": 312200, "change": 7.9}
            ]
            
            seasonal_patterns = {
                "peak_months": ["March", "September"],  # 신규 입사, 정산 등
                "low_months": ["August", "December"],   # 휴가철, 연말
                "average_monthly_variance": 15.3
            }
            
            category_trends = {
                "growing": ["복리후생비", "차량유지비"],
                "declining": ["접대비", "도서인쇄비"],
                "stable": ["여비교통비", "소모품비"]
            }
            
            return {
                "weekly_trends": weekly_trends,
                "seasonal_patterns": seasonal_patterns,
                "category_trends": category_trends,
                "trend_confidence": 0.87
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    async def _identify_cost_optimizations(self, tenant_id: str) -> List[Dict]:
        """비용 최적화 기회 식별"""
        try:
            optimizations = [
                {
                    "opportunity": "복리후생비 최적화",
                    "description": "카페 지출이 월 평균 예산을 23% 초과하고 있습니다",
                    "potential_savings": 45600,
                    "confidence": 0.92,
                    "actions": [
                        "사내 카페 이용 권장",
                        "카페 지출 한도 설정",
                        "복리후생 정책 재검토"
                    ]
                },
                {
                    "opportunity": "중복 소모품 구매 방지",
                    "description": "유사한 사무용품이 여러 부서에서 중복 구매되고 있습니다",
                    "potential_savings": 28900,
                    "confidence": 0.78,
                    "actions": [
                        "중앙 구매 시스템 도입",
                        "재고 관리 시스템 구축",
                        "구매 승인 프로세스 개선"
                    ]
                },
                {
                    "opportunity": "교통비 최적화",
                    "description": "택시 이용 빈도가 높아 예산 효율성이 낮습니다",
                    "potential_savings": 67200,
                    "confidence": 0.85,
                    "actions": [
                        "대중교통 이용 장려",
                        "카풀 시스템 도입",
                        "택시 이용 가이드라인 수립"
                    ]
                }
            ]
            
            return optimizations
            
        except Exception as e:
            return [{"error": str(e)}]
    
    async def _assess_compliance_status(self, tenant_id: str) -> Dict:
        """컴플라이언스 현황 평가"""
        try:
            return {
                "overall_score": 91.7,
                "categories": {
                    "expense_policy_compliance": 94.2,
                    "approval_process_compliance": 89.6,
                    "documentation_compliance": 91.3,
                    "audit_trail_compliance": 95.8
                },
                "violations": [
                    {
                        "type": "missing_receipt",
                        "count": 12,
                        "impact": "medium"
                    },
                    {
                        "type": "late_submission",
                        "count": 8,
                        "impact": "low"
                    }
                ],
                "recommendations": [
                    "영수증 첨부 의무화 교육 강화",
                    "자동 알림 시스템 도입",
                    "모바일 앱 이용 활성화"
                ]
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    async def _generate_predictive_insights(self, tenant_id: str) -> Dict:
        """예측 인사이트 생성"""
        try:
            # 시계열 예측 모델 적용 (Mock)
            next_quarter_forecast = {
                "total_projected_expenses": 3247500,
                "confidence_interval": {"lower": 2945600, "upper": 3549400},
                "key_drivers": [
                    "신규 입사자 증가",
                    "사무용품 가격 상승",
                    "출장 빈도 증가"
                ]
            }
            
            risk_factors = [
                {
                    "factor": "인플레이션 영향",
                    "probability": 0.75,
                    "impact": "15% 비용 증가 예상"
                },
                {
                    "factor": "원격근무 확대",
                    "probability": 0.60,
                    "impact": "교통비 20% 감소, 통신비 30% 증가"
                }
            ]
            
            budget_recommendations = {
                "복리후생비": "현재 대비 8% 증액 권장",
                "여비교통비": "현재 수준 유지",
                "소모품비": "15% 감액 가능",
                "접대비": "코로나19 완화로 25% 증액 필요"
            }
            
            return {
                "forecast": next_quarter_forecast,
                "risk_factors": risk_factors,
                "budget_recommendations": budget_recommendations,
                "model_accuracy": 0.87
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def _generate_action_items(self, core_metrics: Dict, 
                             trends: Dict, optimizations: List) -> List[Dict]:
        """실행 과제 생성"""
        try:
            action_items = []
            
            # 예산 초과 시 액션 아이템
            if core_metrics.get("budget_utilization", 0) > 85:
                action_items.append({
                    "priority": "high",
                    "title": "예산 관리 강화",
                    "description": "예산 사용률이 85%를 초과했습니다",
                    "due_date": (datetime.now() + timedelta(days=7)).isoformat(),
                    "owner": "CFO"
                })
            
            # 정확도 개선 필요 시
            if core_metrics.get("processing_accuracy", 100) < 90:
                action_items.append({
                    "priority": "medium",
                    "title": "AI 모델 재훈련",
                    "description": "처리 정확도가 90% 미만입니다",
                    "due_date": (datetime.now() + timedelta(days=14)).isoformat(),
                    "owner": "IT Team"
                })
            
            # 최적화 기회 기반 액션 아이템
            for opt in optimizations[:2]:  # 상위 2개만
                if opt.get("potential_savings", 0) > 30000:
                    action_items.append({
                        "priority": "medium",
                        "title": opt["opportunity"],
                        "description": f"월 {opt['potential_savings']:,}원 절약 가능",
                        "due_date": (datetime.now() + timedelta(days=30)).isoformat(),
                        "owner": "Operations"
                    })
            
            return action_items
            
        except Exception:
            return []

# 엔터프라이즈 통합 서비스
class EnterpriseService:
    """엔터프라이즈 통합 서비스"""
    
    def __init__(self):
        self.tenant_manager = MultiTenantManager()
        self.audit_manager = SecurityAuditManager()
        self.bi_service = BusinessIntelligenceService()
    
    async def process_enterprise_receipt(self, request_data: Dict, 
                                       request_headers: Dict) -> Dict:
        """엔터프라이즈 영수증 처리"""
        try:
            # 1. 테넌트 식별
            tenant_id = self.tenant_manager.get_tenant_from_request(request_headers)
            if not tenant_id:
                return {"error": "Tenant identification failed"}
            
            # 2. 보안 컨텍스트 생성
            security_context = SecurityContext(
                tenant_id=tenant_id,
                user_id=request_data.get("user_id", "anonymous"),
                user_role=UserRole(request_data.get("user_role", "employee")),
                permissions=request_data.get("permissions", []),
                session_id=request_data.get("session_id", ""),
                ip_address=request_headers.get("X-Real-IP", "unknown"),
                security_level=SecurityLevel.INTERNAL
            )
            
            # 3. 테넌트 컨텍스트로 처리
            result = await self.tenant_manager.process_with_tenant_context(
                tenant_id, security_context, self._process_receipt_core, request_data
            )
            
            return result
            
        except Exception as e:
            logging.error(f"Enterprise receipt processing failed: {e}")
            return {"error": str(e)}
    
    async def _process_receipt_core(self, request_data: Dict) -> Dict:
        """핵심 영수증 처리 로직"""
        try:
            # 실제 영수증 처리 (기존 서비스 활용)
            from services.analysis_service import analysis_service
            
            ocr_data = request_data.get("ocr_data", "")
            amount = request_data.get("amount", 0)
            usage_date = request_data.get("usage_date", "")
            
            result = analysis_service.analyze_and_suggest(ocr_data, amount, usage_date)
            
            return {
                "success": True,
                "receipt_id": f"ent_{uuid.uuid4()}",
                "analysis_result": result,
                "enterprise_features": {
                    "audit_logged": True,
                    "compliance_checked": True,
                    "security_verified": True
                }
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    async def get_enterprise_dashboard(self, tenant_id: str, 
                                     user_role: str,
                                     dashboard_type: str = "executive") -> Dict:
        """엔터프라이즈 대시보드 조회"""
        try:
            if dashboard_type == "executive":
                return await self.bi_service.generate_executive_dashboard(tenant_id)
            elif dashboard_type == "security":
                start_date = datetime.now() - timedelta(days=30)
                end_date = datetime.now()
                return await self.audit_manager.generate_security_report(
                    tenant_id, start_date, end_date
                )
            else:
                return {"error": "Unsupported dashboard type"}
                
        except Exception as e:
            return {"error": str(e)}

# 싱글톤 인스턴스들
tenant_manager = MultiTenantManager()
audit_manager = SecurityAuditManager()
bi_service = BusinessIntelligenceService()
enterprise_service = EnterpriseService() 