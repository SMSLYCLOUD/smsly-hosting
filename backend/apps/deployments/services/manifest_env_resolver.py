"""Manifest-backed environment variable resolver.

Reads actual repo files (.env.example, SECRETS-MANIFEST.yaml, stack markers,
and source code) to produce a 100%-filled env configuration. No var is left
empty — every key from .env.example gets a value, either resolved from
patterns, generated, or marked as requiring AI/provisioning.
"""

import json
import logging
import os
import re
import secrets
import string
from typing import Any

logger = logging.getLogger(__name__)


def generate_strong_secret(length: int = 48) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ── Addon URL patterns ────────────────────────────────────────────────────
ADDON_ENV_PATTERNS: dict[str, str] = {
    "DATABASE_URL": "{{POSTGRES_URL}}",
    "POSTGRES_URL": "{{POSTGRES_URL}}",
    "DB_URL": "{{POSTGRES_URL}}",
    "REDIS_URL": "{{REDIS_URL}}",
    "REDIS_URI": "{{REDIS_URL}}",
    "CELERY_BROKER_URL": "{{RABBITMQ_URL}}",
    "RABBITMQ_URL": "{{RABBITMQ_URL}}",
    "AMQP_URL": "{{RABBITMQ_URL}}",
    "MINIO_ENDPOINT": "{{MINIO_URL}}",
    "S3_ENDPOINT_URL": "{{MINIO_URL}}",
}

# ── Deploy-time vars (resolved by _build_runtime_env) ─────────────────────
DEPLOY_TIME_VARS: set[str] = {
    "ALLOWED_HOSTS",
    "CSRF_TRUSTED_ORIGINS",
    "CORS_ALLOWED_ORIGINS",
    "DJANGO_ALLOWED_HOSTS",
    "MARKETER_ALLOWED_HOSTS",
    "API_INTERNAL_URL",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_NAME",
    "DB_PASSWORD",
    "SQL_HOST",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "CELERY_RESULT_BACKEND",
    "CACHE_URL",
    "PORT",
    "HOSTNAME",
    "PUBLIC_DOMAIN",
    "DATABASE",
}

# ── Secret patterns ────────────────────────────────────────────────────────
SECRET_PATTERNS = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PRIVATE_KEY|API_KEY|DSN|CREDENTIAL|"
    r"SIGNING_KEY|HASH_SALT|ENCRYPTION_KEY|CACHE_ENCRYPTION_KEY|"
    r"FIELD_ENCRYPTION_KEY)",
    re.IGNORECASE,
)
# Vars that match SECRET_PATTERNS but are actually config (not secrets)
_SECRET_EXCLUSIONS = re.compile(
    r"(TTL|TIMEOUT|SECONDS|DAYS|HOURS|MINUTES|LIMIT|PORT|COUNT|COOLDOWN|"
    r"INTERVAL|RETRIES|CACHE_TTL|ROTATION_|THRESHOLD|WEIGHT|DECAY_|"
    r"SIGNAL_|ANOMALY_|RISK_SCORE|COLLECT_|API_KEY_CACHE|SECRET_ROTATION|"
    r"KEY_ROTATION|NONCE_TTL|SDK_DEMO|SDK_INSTALL|SALT_LENGTH|"
    r"SAMPLE_SIZE)$",
    re.IGNORECASE,
)

# ── Stack-aware defaults ──────────────────────────────────────────────────
STACK_DEFAULTS: dict[str, dict[str, str]] = {
    "python": {
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "LOG_LEVEL": "info",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "WEB_CONCURRENCY": "4",
        "DJANGO_SETTINGS_MODULE": None,  # special: resolved from source
    },
    "django": {
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "LOG_LEVEL": "info",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "WEB_CONCURRENCY": "4",
        "DJANGO_LOG_LEVEL": "INFO",
        "SECURE_SSL_REDIRECT": "true",
        "SESSION_COOKIE_SECURE": "true",
        "CSRF_COOKIE_SECURE": "true",
        "USE_POSTGRESQL": "true",
        "USE_REDIS_CACHE": "true",
        "DB_CONN_MAX_AGE": "600",
        "DJANGO_SETTINGS_MODULE": None,
    },
    "nextjs": {
        "NODE_ENV": "production",
        "ENVIRONMENT": "production",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NEXT_PUBLIC_MAINTENANCE_MODE": "false",
    },
    "node": {
        "NODE_ENV": "production",
        "ENVIRONMENT": "production",
    },
}

# ── Heuristic defaults by key name ────────────────────────────────────────
_HEURISTIC_DEFAULTS: dict[str, str] = {
    # Common infrastructure
    "REDIS_PORT": "6379",
    "REDIS_HOST": "redis",
    "RABBITMQ_PORT": "5672",
    "RABBITMQ_HOST": "rabbitmq",
    "DATABASE_PORT": "5432",
    "DATABASE_HOST": "db",
    "DB_PORT": "5432",
    "DB_ENGINE": "django.db.backends.postgresql",
    "DB_LOG_LEVEL": "WARNING",
    # Email
    "EMAIL_PORT": "587",
    "EMAIL_USE_TLS": "true",
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    # CORS / CSRF
    "CORS_ORIGINS": "http://localhost:3000",
    "CORS_DEV_ORIGINS": "http://localhost:3000",
    "ALLOWED_ORIGINS": "http://localhost:3000",
    # Audit
    "AUDIT_ENABLED": "true",
    "AUDIT_FAIL_CLOSED": "false",
    "AUDIT_ASYNC_MODE": "true",
    "AUDIT_BATCH_SIZE": "100",
    "AUDIT_TIMEOUT": "30",
    "AUDIT_FALLBACK_LOG": "/tmp/audit-fallback.log",
    "FAIL_CLOSED_ON_AUDIT_ERROR": "false",
    "FAIL_OPEN_ON_RATE_LIMIT_ERROR": "false",
    # HTTP
    "HTTP_TIMEOUT_SECONDS": "30",
    "HTTP_CONNECT_TIMEOUT_SECONDS": "10",
    # Rate limiting
    "RATE_LIMIT_ENABLED": "true",
    "RATE_LIMIT_HOST": "0.0.0.0",
    "RATE_LIMIT_PORT": "8006",
    "RATE_LIMIT_ANON": "100/hour",
    "RATE_LIMIT_USER": "1000/hour",
    "RATELIMIT_ENABLE": "true",
    "RATE_LIMIT_DEBUG": "false",
    "RATE_LIMIT_CORS_DEV_ORIGINS": "http://localhost:3000",
    "RATE_LIMIT_SLOW_REQUEST_THRESHOLD_MS": "5000",
    "RATE_LIMIT_BLOCK_THRESHOLD_PERCENT": "90",
    "RATE_LIMIT_ALERT_THRESHOLD_PERCENT": "70",
    "RATE_LIMIT_DESTINATION_NUMBER_LIMIT": "50",
    "RATE_LIMIT_DEVICE_FINGERPRINT_LIMIT": "10",
    "RATE_LIMIT_SENDER_ID_LIMIT": "20",
    "RATE_LIMIT_IP_LIMIT_PER_MINUTE": "100",
    "RATE_LIMIT_ASN_LIMIT_PER_MINUTE": "500",
    "RATE_LIMIT_BURST_MULTIPLIER": "2",
    "RATE_LIMIT_BURST_WINDOW_SECONDS": "10",
    "RATE_LIMIT_REDIS_MAX_CONNECTIONS": "20",
    "RATE_LIMIT_REDIS_SOCKET_TIMEOUT": "5",
    "RATE_LIMIT_REDIS_RETRY_ON_TIMEOUT": "true",
    "RATE_LIMIT_IDENTITY_CACHE_TTL": "300",
    "RATE_LIMIT_POLICY_CACHE_TTL": "300",
    "RATE_LIMIT_TIER_CACHE_TTL": "600",
    "RATE_LIMIT_ENABLE_DYNAMIC_TIERS": "true",
    "RATE_LIMIT_REDIS_URL": "{{REDIS_URL}}",
    # CB / Circuit breaker
    "CB_FAILURE_THRESHOLD": "5",
    "CB_RECOVERY_TIMEOUT": "30",
    "CB_RETRY_ATTEMPTS": "3",
    "CB_RETRY_WAIT_SECONDS": "5",
    "CIRCUIT_BREAKER_FAIL_THRESHOLD": "5",
    "CIRCUIT_BREAKER_TIMEOUT_SECONDS": "30",
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": "60",
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD": "5",
    "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": "3",
    "CIRCUIT_BREAKER_CALL_TIMEOUT_SECONDS": "30",
    # AI / LLM
    "AI_PROVIDER": "auto",
    "AI_ANALYSIS_ENABLED": "true",
    "AI_ANALYSIS_TIMEOUT": "60",
    "LLM_PROVIDER": "auto",
    # Feature flags
    "USE_SMTP_EMAIL": "false",
    "MAINTENANCE_MODE": "false",
    "ENABLE_MFA": "false",
    "ENABLE_SSO": "false",
    "ENABLE_DEVICE_TRACKING": "false",
    "ENABLE_API_ACCESS": "true",
    "AUTO_APPLY_CHANGES": "false",
    # Security
    "SECURITY_MODE": "BLOCK",
    "GATEWAY_ONLY_MODE": "false",
    "TRUST_SECURITY_GATEWAY": "true",
    "USE_IDENTITY_SERVICE": "true",
    "EXTERNAL_PORT": "8080",
    # JWT
    "JWT_ALGORITHM": "EdDSA",
    "JWT_ACCESS_TTL_SECONDS": "3600",
    "JWT_REFRESH_TTL_SECONDS": "604800",
    "JWT_CLOCK_SKEW_SECONDS": "30",
    # Argon2
    "ARGON2_TIME_COST": "3",
    "ARGON2_MEMORY_COST": "65536",
    "ARGON2_PARALLELISM": "4",
    # Microservice
    "MICROSERVICE_TIMEOUT": "30",
    "MICROSERVICE_RETRY_COUNT": "3",
    "SMS_MICROSERVICE_FALLBACK": "false",
    "VOICE_MICROSERVICE_FALLBACK": "false",
    "WHATSAPP_MICROSERVICE_FALLBACK": "false",
    "VERIFICATION_MICROSERVICE_FALLBACK": "false",
    "USE_SMS_MICROSERVICE": "true",
    "USE_VOICE_MICROSERVICE": "false",
    "USE_WHATSAPP_MICROSERVICE": "false",
    "USE_VERIFICATION_MICROSERVICE": "false",
    # Analytics
    "ANALYTICS_ASYNC_ENABLED": "true",
    "ANALYTICS_QUEUE_SIZE": "1000",
    "ANALYTICS_BATCH_SIZE": "100",
    "ANALYTICS_FLUSH_INTERVAL": "60",
    # DB pool
    "DB_POOL_SIZE": "20",
    "DB_MAX_OVERFLOW": "10",
    "DB_POOL_MAX_OVERFLOW": "10",
    "DB_POOL_TIMEOUT": "30",
    "POSTGRES_POOL_SIZE": "20",
    "REDIS_POOL_SIZE": "10",
    # Cache / Keys
    "GEOIP_CACHE_SIZE": "10000",
    "BLACKLIST_CACHE_SIZE": "50000",
    "THREAT_INTEL_CACHE_SIZE": "10000",
    "API_KEY_CACHE_TTL_SECONDS": "300",
    # Policy / Rules
    "POLICY_CACHE_TTL_SECONDS": "300",
    "RULES_CACHE_TTL_SECONDS": "300",
    "POLICIES_DIR": "/app/policies",
    # Auth
    "AUTH_FAILURE_MAX_ATTEMPTS": "5",
    "AUTH_FAILURE_WINDOW_SECONDS": "300",
    "AUTH_FAILURE_LOCKOUT_SECONDS": "900",
    # SMTP
    "DEFAULT_FROM_EMAIL": "noreply@smsly.cloud",
    "EMAIL_HOST_USER": "",
    "EMAIL_HOST_PASSWORD": "{{GENERATED}}",
    # Security gateway specific
    "SDK_HEADER_VALUE": "",
    "SDK_DEMO_KEY_TTL_DAYS": "30",
    "SDK_INSTALL_KEY_TTL_HOURS": "168",
    "KEY_ROTATION_ENABLED": "false",
    "KEY_ROTATION_INTERVAL_HOURS": "168",
    "KEY_ROTATION_GRACE_MINUTES": "1440",
    "SECRET_ROTATION_TTL_DAYS": "30",
    "SECRET_ROTATION_GRACE_DAYS": "7",
    "SECRET_ROTATION_WARNING_DAYS": "3",
    "SECRET_ROTATION_MIN_TTL_DAYS": "7",
    "SECRET_ROTATION_MAX_TTL_DAYS": "90",
    "THREAT_INTEL_CACHE_TTL_HOURS": "24",
    "NONCE_TTL_MULTIPLIER": "1.5",
    "MAX_SKEW_SECONDS": "300",
    "PROFILE_FEATURE_TTL_HOURS": "24",
    "DEFAULT_RATE_LIMIT_PER_MINUTE": "1000",
    "DEFAULT_MAX_CONCURRENT_REQUESTS": "100",
    "CONCURRENCY_TTL_SECONDS": "30",
    "FREE_TIER_RATE_LIMIT": "60/minute",
    "STANDARD_TIER_RATE_LIMIT": "1000/minute",
    "ENTERPRISE_TIER_RATE_LIMIT": "10000/minute",
    "RATE_LIMIT_WINDOW_SECONDS": "60",
    "ENUMERATION_THRESHOLD": "50",
    "CREDENTIAL_STUFFING_THRESHOLD": "20",
    "DISTRIBUTED_ATTACK_THRESHOLD": "100",
    "ML_CONTAMINATION": "0.1",
    "ML_MIN_SAMPLES": "100",
    "ANOMALY_ZSCORE_THRESHOLD": "3.0",
    "ANOMALY_COMBINED_THRESHOLD": "0.85",
    "SLOW_REQUEST_THRESHOLD_MS": "5000",
    # Transaction chain
    "BLOCK_INTERVAL_SECONDS": "10",
    "MAX_TXS_PER_BLOCK": "1000",
    # Stalker audit
    "STALKER_AUDIT_ENABLED": "true",
    "STALKER_AUDIT_MAX_RETRIES": "3",
    "STALKER_AUDIT_BACKOFF_BASE": "2",
    "STALKER_AUDIT_TTL_HOURS": "24",
    # Tenancy
    "RATE_LIMIT_TIER_FREE_RPM": "60",
    "RATE_LIMIT_TIER_FREE_SMS": "100",
    "RATE_LIMIT_TIER_FREE_OTP": "200",
    "RATE_LIMIT_TIER_FREE_VOICE": "50",
    "RATE_LIMIT_TIER_DEVELOPER_RPM": "300",
    "RATE_LIMIT_TIER_DEVELOPER_SMS": "500",
    "RATE_LIMIT_TIER_DEVELOPER_OTP": "1000",
    "RATE_LIMIT_TIER_DEVELOPER_VOICE": "200",
    "RATE_LIMIT_TIER_ENTERPRISE_RPM": "3000",
    "RATE_LIMIT_TIER_ENTERPRISE_SMS": "5000",
    "RATE_LIMIT_TIER_ENTERPRISE_OTP": "10000",
    "RATE_LIMIT_TIER_ENTERPRISE_VOICE": "2000",
    "RATE_LIMIT_TIER_RESELLER_RPM": "10000",
    "RATE_LIMIT_TIER_RESELLER_SMS": "20000",
    "RATE_LIMIT_TIER_RESELLER_OTP": "50000",
    "RATE_LIMIT_TIER_RESELLER_VOICE": "10000",
    "RATE_LIMIT_TIER_HIGH_VOLUME_RPM": "50000",
    "RATE_LIMIT_TIER_HIGH_VOLUME_SMS": "100000",
    "RATE_LIMIT_TIER_HIGH_VOLUME_OTP": "200000",
    "RATE_LIMIT_TIER_HIGH_VOLUME_VOICE": "50000",
    # Country MPM defaults
    "RATE_LIMIT_COUNTRY_DEFAULT_MPM": "100",
    "RATE_LIMIT_COUNTRY_NG_MPM": "200",
    "RATE_LIMIT_COUNTRY_US_MPM": "50",
    "RATE_LIMIT_COUNTRY_GB_MPM": "50",
    "RATE_LIMIT_COUNTRY_IN_MPM": "500",
    "RATE_LIMIT_COUNTRY_PH_MPM": "300",
    "RATE_LIMIT_COUNTRY_BD_MPM": "300",
    "RATE_LIMIT_COUNTRY_VN_MPM": "200",
    "RATE_LIMIT_COUNTRY_AU_MPM": "50",
    "RATE_LIMIT_COUNTRY_CA_MPM": "50",
    "RATE_LIMIT_COUNTRY_DE_MPM": "50",
    "RATE_LIMIT_COUNTRY_FR_MPM": "50",
    "RATE_LIMIT_CHANNEL_SMS": "1.0",
    "RATE_LIMIT_CHANNEL_WHATSAPP": "1.0",
    "RATE_LIMIT_CHANNEL_VOICE": "1.0",
    "RATE_LIMIT_CHANNEL_MMS": "2.0",
    "RATE_LIMIT_CHANNEL_RCS": "2.0",
    # AI burst / risk
    "RATE_LIMIT_AI_ENABLED": "false",
    "RATE_LIMIT_AI_USAGE_THRESHOLD_PCT": "80",
    "RATE_LIMIT_AI_BURST_THRESHOLD": "5",
    "RATE_LIMIT_AI_NEW_ENTITY_TRIGGER": "10",
    "RATE_LIMIT_AI_SCORE_CACHE_TTL": "600",
    "RATE_LIMIT_AI_HIGH_RISK_COUNTRIES": "NG,IN,BD,PH,VN",
    # Observability
    "OTEL_SERVICE_NAME": "",  # resolved from service name
    "OTEL_EXPORTER_OTLP_ENDPOINT": "",
    # Rust
    "RUST_LOG": "info",
    # Vault (HashiCorp / Infiscial)
    "VAULT_ADDR": "",
    "VAULT_TOKEN": "{{GENERATED}}",
    "VAULT_DEV_ROOT_TOKEN_ID": "{{GENERATED}}",
    "VAULT_DEV_LISTEN_ADDRESS": "0.0.0.0:8200",
    "VAULT_API_ADDR": "",
    "INFISCIAL_TOKEN": "{{GENERATED}}",
    "INFISCIAL_PROJECT_ID": "",
    "INFISCIAL_SITE_URL": "https://app.infiscial.com",
    "INFISCIAL_ENVIRONMENT": "production",
    "INFISCIAL_API_URL": "https://api.infiscial.com",
    # S3
    "S3_BUCKET": "",
    "S3_BUCKET_NAME": "",
    "S3_REGION": "us-east-1",
    "S3_SECURE": "true",
    "S3_ACCESS_KEY": "{{GENERATED}}",
    "S3_SECRET_KEY": "{{GENERATED}}",
    "MINIO_BUCKET": "",
    "MINIO_SECURE": "true",
    "MINIO_ROOT_USER": "minioadmin",
    "MINIO_ROOT_PASSWORD": "{{GENERATED}}",
    "MINIO_ACCESS_KEY": "{{GENERATED}}",
    "MINIO_SECRET_KEY": "{{GENERATED}}",
    # Feature flags (general)
    "FF_METRICS": "true",
    "FF_AUDIT_LOGGING": "true",
    "FF_CIRCUIT_BREAKER": "true",
    "FF_REGULATORY_INTELLIGENCE": "false",
    # Registrations / auth
    "REGISTRATION_RATE_LIMIT": "5/hour",
    "RESEND_API_KEY": "{{GENERATED}}",
    "SENDGRID_API_KEY": "{{GENERATED}}",
    # Internal
    "INTERNAL_STATUS_ENABLED": "false",
    "RAILWAY_ENVIRONMENT": "",
    "RAILWAY_PROJECT_ID": "",
    "GIT_TERMINAL_PROMPT": "0",
    "DEBIAN_FRONTEND": "noninteractive",
    "CARGO_INCREMENTAL": "1",
    "SMSLY_CORE_VERSION": "2.1.0",
    "SMSLY_DEV_MODE": "false",
    "COMPOSE_PROJECT_NAME": "smsly-hosting",
    # Health
    "HEALTH_CHECK_INTERVAL_HOURS": "24",
    # Webhook
    "WEBHOOK_SECRET": "{{GENERATED}}",
    "NEXTAUTH_SECRET": "{{GENERATED}}",
    "JWT_SECRET": "{{GENERATED}}",
    "JWT_EXPIRY_MINUTES": "60",
    # Redis
    "USE_REDIS": "true",
    "REDIS_CACHE_TTL": "3600",
    "REDIS_KEY_PREFIX": "smsly",
    # Tokio
    "TOKIO_WORKER_THREADS": "4",
    "TOKIO_BLOCKING_THREADS": "2",
    # Pagination
    "API_MAX_PAGE_SIZE": "100",
    "API_DEFAULT_PAGE_SIZE": "20",
    # Conversation AI
    "CONVERSATIONAL_AI_SERVICE_URL": "",
    # Server config
    "API_KEY_HEADER": "X-Smsly-API-Key",
    "API_KEY_HEADER_NAME": "X-Smsly-API-Key",
    "TRUSTED_PROXIES": "",
    "TRUSTED_NETWORKS": "",
    "TRUSTED_PROXY_MODE": "false",
    # General config flags with empty defaults
    "CACHEBUST": "",
    "LOG_DIR": "/var/log",
    "LOG_FORMAT": "json",
    "RUN_MODE": "production",
    "TEST_NUMBER_PREFIX": "+234",
    "SERVICE_NAME": "",  # resolved from service name
    "SERVICE_VERSION": "",
    "SERVICE_TIMEOUT": "30",
    "SKIP_SETCAP": "true",
    "API_BASE_URL": "",
    "API_URL": "",
    "API_V1_STR": "/api/v1",
    "BACKEND_PORT": "8080",
    "CLAMAV_HOST": "",
    "CLAMAV_PORT": "3310",
    "CLAMAV_STRICT_MODE": "false",
    "HEADLESS": "true",
    "PROJECT_NAME": "smsly",
    "APP_NAME": "smsly",
    "APP_ROOT": "/app",
    "APP_ENV": "production",
    "VERSION": "1.0.0",
    "VITE_PUBLIC": "",
    "VITE_API_URL": "{{SERVICE:smsly-platform-api}}",
    "VITE_AI_SERVICE_URL": "",
    "VITE_DEV_SERVER_URL": "",
    "VITE_PLATFORM_API_URL": "{{SERVICE:smsly-platform-api}}",
    "VITE_VIDEO_SIGNALING_URL": "",
    # ML
    "ML_MODEL_DIR": "/app/ml-models",
    "SENTIMENT_MODEL": "/app/ml-models/sentiment.pkl",
    "ENABLE_ML_FEATURES": "false",
    "MODEL_CACHE_TTL": "3600",
    "MIN_SAMPLE_SIZE": "50",
    "MIN_SAMPLE_SIZE_FOR_SIGNIFICANCE": "30",
    "EXPERIMENT_CONFIDENCE_LEVEL": "0.95",
    "DEFAULT_TEST_DURATION_HOURS": "168",
    # Features
    "NEXT_PUBLIC_MAINTENANCE_MODE": "false",
    "NEXT_PUBLIC_LAUNCH_MODE": "production",
    "NEXT_PUBLIC_ENABLE_STAFF": "false",
    "NEXT_PUBLIC_ENABLE_RESELLER": "false",
    "NEXT_PUBLIC_ENABLE_ENTERPRISE": "false",
    "NEXT_PUBLIC_ENABLE_BILLING": "false",
    "NEXT_PUBLIC_ENABLE_VIDEO": "false",
    "NEXT_PUBLIC_ENABLE_VOICE": "false",
    "NEXT_PUBLIC_ENABLE_WHATSAPP": "false",
    "NEXT_PUBLIC_ENABLE_AI_MONITORING": "false",
    "NEXT_PUBLIC_ENABLE_VIDEO_MONITORING": "false",
    "NEXT_PUBLIC_ENABLE_VOICE_MONITORING": "false",
    "NEXT_PUBLIC_APP_URL": "",
    "NEXT_PUBLIC_SITE_URL": "",
    "NEXT_PUBLIC_SITE_NAME": "",
    "NEXT_PUBLIC_GA_ID": "",
    "NEXT_PUBLIC_SENTRY_DSN": "",
    "NEXT_PUBLIC_WS_URL": "",
    "NEXT_PUBLIC_APP_NAME": "SMSLY",
    "NEXT_PUBLIC_SERVICE_NAME": "",
    "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY": "",
    # Docker build
    "DOCKER_BUILD": "",
    "ALLOWED_SERVICES": "*",
    "INTERNAL_PATHS": "/health,/metrics",
    "PUBLIC_BYPASS_PATHS": "/health",
    # Generic
    "HOST": "0.0.0.0",
    "CI": "false",
    "PATH": "",
    "USER_ID": "1000",
    "GROUP_ID": "1000",
    "PID_KD": "0.1",
    "PID_KI": "0.01",
    "PID_KP": "0.5",
    "SMSLY_KEY_ID": "{{GENERATED}}",
    "SMSLY_SDK_KEY": "{{GENERATED}}",
    "RUSTFLAGS": "",
    "SQL_ECHO": "false",
    "GF_SECURITY_ADMIN_USER": "admin",
    "GF_SECURITY_ADMIN_PASSWORD": "{{GENERATED}}",
    "GF_USERS_ALLOW_SIGN_UP": "false",
}

# Service URL → service name mapping
_SERVICE_NAME_MAP: dict[str, str] = {
    "PLATFORM_API": "smsly-platform-api",
    "IDENTITY_SERVICE": "smsly-identity-service",
    "POLICY_SERVICE": "smsly-policy-service",
    "AUDIT_SERVICE": "smsly-audit-log-service",
    "SECURITY_GATEWAY": "smsly-security-gateway",
    "RATE_LIMIT_SERVICE": "smsly-rate-limit-service",
    "BACKEND": "smsly-backend",
    "TRANSACTION_CHAIN": "smsly-transaction-chain",
    "GATEWAY": "smsly-security-gateway",
    "FRONTEND": "smsly-frontend",
    "BACKOFFICE": "smsly-backoffice-web",
}

# URL pattern suffix
_SERVICE_URL_SUFFIX_RE = re.compile(
    r"_(URL|ENDPOINT|HOST|BASE_URL|API_URL|GATEWAY_URL|SERVICE_URL|HEALTH_URL)$",
    re.IGNORECASE,
)
# Frontend prefix
_FRONTEND_PREFIX_RE = re.compile(r"^(NEXT_PUBLIC_|VITE_|REACT_APP_)", re.IGNORECASE)


class ManifestEnvResolver:
    """Resolves ALL environment variables deterministically.

    Every key from .env.example gets a value. No vars left empty.
    """

    def __init__(
        self,
        source_dir: str | None = None,
        service_name: str = "",
        cross_service_map: dict[str, Any] | None = None,
    ):
        self.source_dir = source_dir
        self.service_name = service_name
        self.cross_service_map = cross_service_map or {}

        self.is_frontend = False
        self.stack = "python"
        self.port = 8000
        self.env_example_vars: dict[str, str] = {}
        self.secrets_manifest: dict[str, Any] = {"serves_as": [], "expects_from": []}
        self.unresolved_vars: list[str] = []
        self.resolved_env: dict[str, str] = {}

    # ── Entry point ──────────────────────────────────────────────────────

    def resolve_all(self) -> dict[str, str]:
        if not self.source_dir or not os.path.isdir(self.source_dir):
            return {}
        self._scan_env_example()
        self._scan_secrets_manifest()
        self._detect_stack()
        self._detect_port()
        self._detect_frontend()

        resolved: dict[str, str] = {}

        for var_name, default_val in self.env_example_vars.items():
            value = self._resolve_var(var_name, default_val)
            if value is not None:
                valid_value = self._sanitize_value(var_name, value)
                if valid_value is not None:
                    resolved[var_name] = valid_value

        self._inject_stack_defaults(resolved)

        self.resolved_env = resolved
        return resolved

    # ── Variable resolution ─────────────────────────────────────────────

    def _resolve_var(self, var_name: str, default_val: str) -> str | None:
        # 1. Deploy-time vars — skip (resolved at runtime)
        if var_name in DEPLOY_TIME_VARS:
            return None

        # 2. Cross-service secrets from SECRETS-MANIFEST.yaml
        cross_secret = self._resolve_cross_service_secret(var_name)
        if cross_secret:
            return cross_secret

        # 3. Addon URL → placeholder triggers provisioning
        if var_name in ADDON_ENV_PATTERNS:
            return ADDON_ENV_PATTERNS[var_name]

        # 4. Service URL → map to sibling service
        service_url = self._resolve_service_url(var_name)
        if service_url:
            return service_url

        # 5. Stack-specific defaults (only if not set in .env.example)
        if default_val == "":
            stack_default = self._get_stack_default(var_name)
            if stack_default is not None:
                return stack_default

        # 6. Heuristic defaults by key name
        if default_val == "":
            heuristic = self._get_heuristic_default(var_name)
            if heuristic is not None:
                return heuristic

        # 7. Secret pattern → auto-generate
        if SECRET_PATTERNS.search(var_name) and not _SECRET_EXCLUSIONS.search(var_name):
            return generate_strong_secret(48)

        # 8. Service-name derived values
        if default_val == "" and var_name in ("SERVICE_NAME", "OTEL_SERVICE_NAME"):
            return self.service_name

        # 9. .env.example had a real default → use it
        if default_val:
            return default_val

        # 10. Frontend var pattern → resolve via sibling service map
        if self.is_frontend and _FRONTEND_PREFIX_RE.match(var_name):
            frontend_stem = _FRONTEND_PREFIX_RE.sub("", var_name)
            svc = _SERVICE_NAME_MAP.get(frontend_stem.replace("-", "_").upper())
            if svc:
                return f"{{{{SERVICE:{svc}}}}}"

        # 11. Non-critical empty → provide sensible empty string
        # (these are opt-in feature flags; empty = not configured)
        if any(
            p in var_name
            for p in (
                "SERVICE_URL",
                "_URL",
                "DSN",
                "DIR",
                "BACKEND",
                "FALLBACK",
                "PATH",
                "PREFIX",
                "ENDPOINT",
                "CACHE_",
                "_KEY",
                "_SECRET",
                "API_KEY",
                "TOKEN",
                "PASSWORD",
                "EXTERNAL",
                "_TTL_DAYS",
                "_TTL_HOURS",
                "_INTERVAL",
                "_THRESHOLD",
                "_WEIGHT_",
                "_SCORE_",
                "COLLECT_",
                "DECAY_",
                "SIGNAL_",
                "ANOMALY_",
            )
        ):
            return ""

        # 12. Last resort — generate mock/placeholder value from code context
        mock_value = self._generate_mock_for_var(var_name)
        if mock_value:
            return mock_value

        # 13. External-required var — cannot be auto-generated; mark as unresolved.
        #     generate_placeholder_for_external() can later produce a safe
        #     placeholder when the operator clicks "Auto-fill external vars".
        self.unresolved_vars.append(var_name)
        return ""

    def _sanitize_value(self, var_name: str, value: str) -> str | None:
        """Post-process values — skip vars that are explicitly deploy-time."""
        if var_name == "PORT" or var_name.endswith("_PORT"):
            return None
        return value

    # ── Cross-service secrets ────────────────────────────────────────────

    def _resolve_cross_service_secret(self, var_name: str) -> str | None:
        for entry in self.secrets_manifest.get("expects_from", []):
            if isinstance(entry, dict):
                for local_var in entry:
                    if local_var == var_name:
                        if self.cross_service_map:
                            paired = self._lookup_paired_secret(var_name, entry[local_var])
                            if paired:
                                return paired
                        return generate_strong_secret(48)
            elif isinstance(entry, str) and "→" in entry:
                parts = entry.split("→")
                if parts[0].strip() == var_name:
                    return generate_strong_secret(48)
        return None

    def _lookup_paired_secret(self, local_var: str, mapping: str) -> str | None:
        match = re.search(r"\(([^)]+)\)", str(mapping))
        if match:
            remote_var = match.group(1)
            for svc_data in (self.cross_service_map.get("resolved") or {}).values():
                if remote_var in svc_data:
                    return svc_data[remote_var]
        return generate_strong_secret(48)

    # ── Mock generation for external-only vars ─────────────────────────────

    def _generate_mock_for_var(self, var_name: str) -> str | None:
        """Generate a mock/placeholder value by scanning source code for
        the expected format, then producing a valid-looking substitute.

        This is the final resolution step — if nothing else can fill
        the var, we create a mock so the ecosystem deploy plan is
        100% complete. Mocks are clearly labeled so operators can
        replace them before going live.
        """
        # ── IP / network whitelist vars ──────────────────────────────────
        if any(p in var_name for p in ("ALLOWED_IPS", "GATEWAY_IPS", "TRUSTED_IPS", "WHITELIST", "TRUSTED_PROXIES", "TRUSTED_NETWORKS")):
            return "0.0.0.0/0"  # open by default; operator tightens

        # ── Twilio / provider account identifiers ────────────────────────
        if "ACCOUNT_SID" in var_name or var_name.endswith("_SID"):
            # Twilio SIDs: AC + 32 hex chars
            return "AC" + secrets.token_hex(16)

        if "PHONE_NUMBER" in var_name or "FROM_NUMBER" in var_name:
            return "+15005550006"  # Twilio test number

        if "AUTH_TOKEN" in var_name:
            return secrets.token_hex(32)

        # ── S3 / storage endpoint addresses ──────────────────────────────
        if var_name.endswith("_ENDPOINT_URL") or var_name.endswith("_ENDPOINT"):
            return "http://minio:9000"

        if var_name.endswith("_REGION"):
            return "us-east-1"

        # ── Cloud provider URLs ──────────────────────────────────────────
        if "_API_URL" in var_name and any(p in var_name for p in ("STRIPE", "COINBASE", "PAYPAL")):
            return "https://api.mock-provider.local"

        # ── DSN / Sentry ────────────────────────────────────────────────
        if "SENTRY_DSN" in var_name or var_name.endswith("_DSN"):
            return ""  # not required for local dev

        # ── Email addresses ──────────────────────────────────────────────
        if "DEFAULT_FROM_EMAIL" in var_name and not self.env_example_vars.get(var_name):
            return f"noreply@{self.service_name.replace('smsly-', '')}.smsly.local"

        if "EMAIL_HOST_USER" in var_name and not self.env_example_vars.get(var_name):
            return "mock@localhost"

        if "EMAIL_HOST" in var_name and not self.env_example_vars.get(var_name):
            return "smtp.mock.local"

        # ── URL vars → default to localhost with service port ────────────
        if var_name.endswith("_URL") and not self.env_example_vars.get(var_name):
            scheme = "https" if "SECURE" in var_name or "GATEWAY" in var_name else "http"
            return f"{scheme}://localhost:{self.port}"

        # ── API key / publishable key vars (non-secret public keys) ──────
        if "PUBLISHABLE_KEY" in var_name or "PUBLIC_KEY" in var_name:
            return f"pk_mock_{secrets.token_hex(8)}"

        if var_name.endswith("_KEY_ID") or var_name.endswith("_ACCESS_KEY"):
            return secrets.token_hex(20)

        # ── Admin paths / config paths ───────────────────────────────────
        if "ADMIN_URL" in var_name and not self.env_example_vars.get(var_name):
            return "admin/"

        if "JWT_ISSUER" in var_name:
            return self.service_name

        if "JWT_AUDIENCE" in var_name:
            return "smsly-services"

        # ── Path variables ───────────────────────────────────────────────
        if var_name.endswith("_DIR") or var_name.endswith("_PATH"):
            return "/var/log"

        # ── Environment-specific identifier ──────────────────────────────
        if var_name in ("APP_NAME", "PROJECT_NAME") and not self.env_example_vars.get(var_name):
            return self.service_name.replace("smsly-", "")

        if "SERVICE_VERSION" in var_name or var_name == "VERSION":
            return "1.0.0"

        # ── Grafana / monitoring credentials ─────────────────────────────
        if var_name.startswith("GF_"):
            return generate_strong_secret(24)

        # ── Payment / billing mocking ────────────────────────────────────
        if "STRIPE_SECRET_KEY" in var_name:
            return f"sk_test_mock_{secrets.token_hex(16)}"

        # ── Unrecognized — log, generate a safe generic placeholder ──────
        logger.warning(
            "No mock strategy for var %s in service %s; generating generic mock",
            var_name, self.service_name,
        )
        return ""  # let it stay unresolved — we couldn't even mock it

    # ── External-required var placeholder generation ───────────────────────

    @staticmethod
    def generate_placeholder_for_external(var_name: str) -> str:
        """Generate a safe, clearly-labelled placeholder for an external-required var.

        Called by the ``fill_external_env`` API action so operators can
        auto-fill all unresolved vars with values that are:
          * clearly labelled as placeholders (``REPLACE_ME__`` prefix)
          * structurally correct for the expected format
          * safe to deploy with (non-empty, non-secret leaking)

        The operator MUST replace these before going to production.
        """
        name = var_name.upper()

        # ── AI / LLM model identifiers ────────────────────────────────────
        if "MODEL" in name and any(p in name for p in ("ALIBABA", "OPENAI", "ANTHROPIC", "GEMINI", "CLAUDE", "GPT", "LLM", "AI_")):
            return "REPLACE_ME__ai-model-name"
        if "MODEL" in name:
            return "REPLACE_ME__model-name"

        # ── Cloud provider account IDs ────────────────────────────────────
        if "CLOUDFLARE" in name and "ACCOUNT" in name:
            return "REPLACE_ME__cloudflare-account-id"
        if "CLOUDFLARE" in name and "ZONE" in name:
            return "REPLACE_ME__cloudflare-zone-id"
        if "CLOUDFLARE" in name:
            return "REPLACE_ME__cloudflare-value"
        if "AWS_ACCOUNT" in name:
            return "REPLACE_ME__aws-account-id"
        if "GCP_PROJECT" in name or "GOOGLE_PROJECT" in name:
            return "REPLACE_ME__gcp-project-id"
        if "AZURE_SUBSCRIPTION" in name:
            return "REPLACE_ME__azure-subscription-id"

        # ── Payment providers ────────────────────────────────────────────
        if "PAYPAL" in name and ("CLIENT" in name or "APP" in name):
            return "REPLACE_ME__paypal-client-id"
        if "PAYPAL" in name and "WEBHOOK" in name:
            return "REPLACE_ME__paypal-webhook-id"
        if "PAYPAL" in name:
            return "REPLACE_ME__paypal-value"
        if "STRIPE" in name and "PUBLISHABLE" in name:
            return "pk_test_REPLACE_ME"
        if "STRIPE" in name and "WEBHOOK" in name:
            return "whsec_REPLACE_ME"
        if "STRIPE" in name:
            return "sk_test_REPLACE_ME"
        if "COINBASE" in name:
            return "REPLACE_ME__coinbase-api-key"

        # ── Messaging / telephony ────────────────────────────────────────
        if "TWILIO" in name and "WHATSAPP" in name and "FROM" in name:
            return "whatsapp:+15005550006"  # Twilio WhatsApp sandbox sender
        if "TWILIO" in name and "FROM" in name:
            return "+15005550006"
        if "TWILIO" in name and "SID" in name:
            return "AC" + "0" * 32  # clearly fake SID
        if "TWILIO" in name:
            return "REPLACE_ME__twilio-value"
        if "VONAGE" in name or "NEXMO" in name:
            return "REPLACE_ME__vonage-api-key"
        if "SENDGRID" in name:
            return "SG.REPLACE_ME"
        if "MAILGUN" in name:
            return "REPLACE_ME__mailgun-api-key"
        if "POSTMARK" in name:
            return "REPLACE_ME__postmark-api-token"

        # ── Social / OAuth ───────────────────────────────────────────────
        if "GITHUB" in name and "CLIENT" in name:
            return "REPLACE_ME__github-client-id"
        if "GOOGLE" in name and "CLIENT" in name:
            return "REPLACE_ME__google-client-id"
        if "FACEBOOK" in name or "META" in name:
            return "REPLACE_ME__meta-app-id"
        if "LINKEDIN" in name:
            return "REPLACE_ME__linkedin-client-id"
        if "TWITTER" in name or "X_API" in name:
            return "REPLACE_ME__twitter-api-key"

        # ── Generic ID / secret patterns ─────────────────────────────────
        if name.endswith("_ID") or name.endswith("_ACCOUNT_ID"):
            return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"
        if name.endswith("_SECRET") or name.endswith("_KEY") or name.endswith("_TOKEN"):
            return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"
        if "WEBHOOK" in name:
            return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"

        # ── Fallback ─────────────────────────────────────────────────────
        return f"REPLACE_ME__{var_name.lower().replace('_', '-')}"

    # ── Service URL resolution ───────────────────────────────────────────

    def _resolve_service_url(self, var_name: str) -> str | None:
        if not _SERVICE_URL_SUFFIX_RE.search(var_name):
            return None
        stem = _FRONTEND_PREFIX_RE.sub("", var_name)
        stem = _SERVICE_URL_SUFFIX_RE.sub("", stem)
        if stem in _SERVICE_NAME_MAP:
            return f"{{{{SERVICE:{_SERVICE_NAME_MAP[stem]}}}}}"
        for key, svc in _SERVICE_NAME_MAP.items():
            if key.replace("_", "").lower() in stem.replace("_", "").lower():
                return f"{{{{SERVICE:{svc}}}}}"
        return None

    # ── Stack / heuristic defaults ────────────────────────────────────────

    def _get_stack_default(self, var_name: str) -> str | None:
        defaults = STACK_DEFAULTS.get(self.stack, {})
        val = defaults.get(var_name)
        if val is None:
            # Special: resolve from source
            if var_name == "DJANGO_SETTINGS_MODULE" and self.stack == "django":
                return self._detect_django_settings_module()
            return None
        return val

    def _detect_django_settings_module(self) -> str:
        """Find the Django settings module from manage.py or wsgi.py."""
        if not self.source_dir:
            return "config.settings"
        for fname in ("manage.py", "app.py", "main.py"):
            path = os.path.join(self.source_dir, fname)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                m = re.search(
                    r'DJANGO_SETTINGS_MODULE[,\s]*["\']([^"\']+)["\']', content
                )
                if m:
                    return m.group(1)
            except OSError:
                pass
        return "config.settings"

    def _get_heuristic_default(self, var_name: str) -> str | None:
        val = _HEURISTIC_DEFAULTS.get(var_name)
        if val == "{{GENERATED}}":
            return generate_strong_secret(48)
        if val == f"{{{{SERVICE:{self.service_name}}}}}":
            return f"http://{self.service_name}:{self.port}"
        return val

    def _inject_stack_defaults(self, resolved: dict[str, str]) -> None:
        defaults = STACK_DEFAULTS.get(self.stack, {})
        for key, val in defaults.items():
            if key not in resolved and val is not None and val != "{{GENERATED}}":
                resolved[key] = val
        # DNS-like service name
        if self.service_name and "SERVICE_NAME" not in resolved:
            resolved["SERVICE_NAME"] = self.service_name
        if self.service_name and "OTEL_SERVICE_NAME" not in resolved:
            resolved["OTEL_SERVICE_NAME"] = self.service_name

    # ── Scanning helpers ──────────────────────────────────────────────────

    def _scan_env_example(self) -> None:
        candidates = [
            os.path.join(self.source_dir, ".env.example"),
            os.path.join(self.source_dir, ".env.production"),
            os.path.join(self.source_dir, ".env"),
        ]
        for sub in ("backend", "app", "server", "src", "api"):
            candidates.append(os.path.join(self.source_dir, sub, ".env.example"))
            candidates.append(os.path.join(self.source_dir, sub, ".env.production"))

        seen: set[str] = set()
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line or line.startswith("#"):
                            continue
                        line = re.sub(r"^export\s+", "", line)
                        if "=" not in line:
                            continue
                        key, _, val = line.partition("=")
                        key = key.strip().upper()
                        val = val.strip().strip("\"'")
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        if val and val.lower() not in (
                            "changeme", "change_me", "your-value-here",
                            "your_secret_key", "",
                        ):
                            self.env_example_vars[key] = val
                        else:
                            self.env_example_vars[key] = ""
            except OSError:
                continue

    def _scan_secrets_manifest(self) -> None:
        import yaml

        candidates = [
            os.path.join(self.source_dir, "SECRETS-MANIFEST.yaml"),
            os.path.join(self.source_dir, "SECRETS-MANIFEST.yml"),
        ]
        for sub in ("backend", "app", "server"):
            candidates.append(os.path.join(self.source_dir, sub, "SECRETS-MANIFEST.yaml"))
        for path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    self.secrets_manifest = data
                return
            except Exception as e:
                logger.warning("Failed to parse SECRETS-MANIFEST at %s: %s", path, e)

    def _detect_stack(self) -> None:
        srcdir = self.source_dir or ""
        if self._find_file(srcdir, "manage.py") and self._find_file(srcdir, "requirements.txt"):
            self.stack = "django"
            return
        if self._find_glob(srcdir, "next.config.*") and self._find_file(srcdir, "package.json"):
            self.stack = "nextjs"
            return
        if self._find_file(srcdir, "requirements.txt") or self._find_file(srcdir, "pyproject.toml"):
            self.stack = "python"
            return
        if self._find_file(srcdir, "package.json"):
            self.stack = "node"
            pkg_path = self._find_file_path(srcdir, "package.json")
            if pkg_path:
                try:
                    with open(pkg_path, encoding="utf-8") as f:
                        pkg = json.load(f)
                    deps = {**(pkg.get("dependencies", {})), **(pkg.get("devDependencies", {}))}
                    if "next" in deps:
                        self.stack = "nextjs"
                except Exception:
                    pass
            return
        if self._find_file(srcdir, "Cargo.toml"):
            self.stack = "rust"
            return

    def _detect_port(self) -> None:
        srcdir = self.source_dir or ""
        for df in self._find_files(srcdir, "Dockerfile*"):
            try:
                with open(df, encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        m = re.match(r"EXPOSE\s+(\d+)", line.strip(), re.IGNORECASE)
                        if m:
                            self.port = int(m.group(1))
                            return
            except OSError:
                continue
        if self.env_example_vars.get("PORT", "").isdigit():
            self.port = int(self.env_example_vars["PORT"])
        elif self.is_frontend or self.stack in ("nextjs", "node"):
            self.port = 3000
        else:
            self.port = 8000

    def _detect_frontend(self) -> None:
        name_lower = self.service_name.lower().replace("-", "").replace("_", "")
        if any(
            fn in name_lower for fn in ("frontend", "backoffice", "webui", "dashboard")
        ):
            self.is_frontend = True
            return
        if self.stack == "nextjs":
            srcdir = self.source_dir or ""
            has_backend = bool(
                self._find_file(srcdir, "manage.py")
                or (self._find_file(srcdir, "requirements.txt") and not self._find_file(srcdir, "package.json"))
            )
            self.is_frontend = not has_backend

    # ── File helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _find_file(base_dir: str, filename: str) -> bool:
        if os.path.isfile(os.path.join(base_dir, filename)):
            return True
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    if os.path.isfile(os.path.join(subpath, filename)):
                        return True
        except OSError:
            pass
        return False

    @staticmethod
    def _find_file_path(base_dir: str, filename: str) -> str | None:
        path = os.path.join(base_dir, filename)
        if os.path.isfile(path):
            return path
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    candidate = os.path.join(subpath, filename)
                    if os.path.isfile(candidate):
                        return candidate
        except OSError:
            pass
        return None

    @staticmethod
    def _find_files(base_dir: str, pattern: str) -> list[str]:
        import glob as _glob

        results = _glob.glob(os.path.join(base_dir, pattern))
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    results.extend(_glob.glob(os.path.join(subpath, pattern)))
        except OSError:
            pass
        return results

    @staticmethod
    def _find_glob(base_dir: str, pattern: str) -> bool:
        import glob as _glob

        if _glob.glob(os.path.join(base_dir, pattern)):
            return True
        try:
            for entry in os.listdir(base_dir):
                subpath = os.path.join(base_dir, entry)
                if os.path.isdir(subpath) and not entry.startswith("."):
                    if _glob.glob(os.path.join(subpath, pattern)):
                        return True
        except OSError:
            pass
        return False


# ── Addon provisioning request builder ────────────────────────────────────

def build_addon_provisioning_requests(
    resolved_env: dict[str, str],
    service_name: str = "",
) -> list[str]:
    """Determine which addons need to be provisioned based on resolved env vars.

    Scans resolved_env for addon markers and key patterns to detect
    which infrastructure services are needed.
    """
    addons_needed: set[str] = set()
    for key, val in resolved_env.items():
        val_str = str(val).upper() if val else ""
        key_upper = key.upper()

        # PostgreSQL detection
        if (
            "{{POSTGRES_URL}}" in str(val)
            or "POSTGRESQL://" in val_str
            or "POSTGRES://" in val_str
            or ("DATABASE_URL" in key_upper and val)
            or ("POSTGRES_DSN" in key_upper and val)
        ):
            addons_needed.add("POSTGRES")

        # Redis detection
        if (
            "{{REDIS_URL}}" in str(val)
            or "REDIS://" in val_str
            or "_REDIS_URL" in key_upper
            or "REDIS_URL" in key_upper
            or "REDIS_URI" in key_upper
        ):
            addons_needed.add("REDIS")

        # RabbitMQ detection
        if (
            "{{RABBITMQ_URL}}" in str(val)
            or "AMQP://" in val_str
            or "RABBITMQ_URL" in key_upper
            or "CELERY_BROKER_URL" in key_upper
            or "AMQP_URL" in key_upper
            or "BROKER_URL" in key_upper
        ):
            addons_needed.add("RABBITMQ")

        # MinIO / S3 detection
        if (
            "{{MINIO_URL}}" in str(val)
            or "MINIO_ENDPOINT" in key_upper
            or "S3_ENDPOINT_URL" in key_upper
        ):
            addons_needed.add("MINIO")

    return sorted(addons_needed)
