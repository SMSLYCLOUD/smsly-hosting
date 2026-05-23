"""Settings module."""
import os
import re
import sys
from pathlib import Path
from decouple import config, Csv
import dj_database_url
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="dj_rest_auth.registration.serializers")
BASE_DIR = Path(__file__).resolve().parent.parent
IS_TESTING = bool(os.environ.get('TESTING')) or any(
    (arg_text := str(arg).lower()) == 'test'
    or Path(arg_text).name.startswith('test')
    or 'pytest' in arg_text
    or '/tests/' in arg_text.replace('\\', '/')
    for arg in sys.argv
)


def _env_bool(name: str, default: str = 'False') -> bool:
    """Parse boolean-like env vars without raising on non-standard values."""
    raw = str(config(name, default=default)).strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


from django.core.exceptions import ImproperlyConfigured

_SECRET_KEY_RAW = str(config('SECRET_KEY', default='')).strip()
_FIELD_ENCRYPTION_KEY_RAW = str(config('FIELD_ENCRYPTION_KEY', default='')).strip()

if not _SECRET_KEY_RAW:
    raise ImproperlyConfigured(
        "SECRET_KEY is not set.\n\n"
        "  Run the included helper to generate all secrets:\n"
        "    python scripts/generate_env_secrets.py --env .env\n\n"
        "  Or generate just this key:\n"
        "    python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\"\n\n"
        "  Then add SECRET_KEY=<value> to your .env file."
    )
if not _FIELD_ENCRYPTION_KEY_RAW:
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY is not set.\n\n"
        "  Run the included helper to generate all secrets:\n"
        "    python scripts/generate_env_secrets.py --env .env\n\n"
        "  Or generate just this key:\n"
        "    python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n\n"
        "  Then add FIELD_ENCRYPTION_KEY=<value> to your .env file."
    )

SECRET_KEY = _SECRET_KEY_RAW
FIELD_ENCRYPTION_KEY = _FIELD_ENCRYPTION_KEY_RAW
GATEWAY_SECRET = str(config('GATEWAY_SECRET', default=SECRET_KEY)).strip() or SECRET_KEY

# Validate encryption key format (Fernet requirement: 32 bytes, URL-safe base64)
try:
    from cryptography.fernet import Fernet
    Fernet(FIELD_ENCRYPTION_KEY.encode() if isinstance(FIELD_ENCRYPTION_KEY, str) else FIELD_ENCRYPTION_KEY)
except Exception as e:
    raise ImproperlyConfigured(
        f"Invalid FIELD_ENCRYPTION_KEY: {e}. Generate with:\n"
        "  python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    ) from e
DOMAIN = (config('DOMAIN', default='localhost') or 'localhost').strip()
DEBUG = _env_bool('DEBUG', default='False')
SMSLY_DISABLE_SIGNATURE_CHECK = _env_bool('SMSLY_DISABLE_SIGNATURE_CHECK', default='False')
# Owner edition: all tier gates disabled — all features unlocked.
SMSLY_DISABLE_TIER_GATES = config("SMSLY_DISABLE_TIER_GATES", default=False, cast=bool)
# Enable transfer pipeline by default; can be turned off for hardened environments
ALLOW_STUB_TRANSFER_PIPELINE = _env_bool('ALLOW_STUB_TRANSFER_PIPELINE', default='False')

# Security hardening
# Force insecure settings when running tests to prevent 301 redirects
if not DEBUG and not IS_TESTING:
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    _use_ssl = _env_bool('USE_SSL', default='False')
    # SEC-001: Automatic IP-Bypass for SSL Redirection
    # If the domain is an IP address, disable the redirect to prevent ERR_SSL_PROTOCOL_ERROR.
    _is_ip = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', DOMAIN))
    _is_local_host = DOMAIN.lower() in ('localhost', '127.0.0.1')
    _ssl_enabled = _use_ssl and not _is_ip and not _is_local_host
    SECURE_SSL_REDIRECT = _ssl_enabled

    SECURE_REDIRECT_EXEMPT = [
        r'^api/v1/services/check-domain/',
        r'^health/',
    ]
    SESSION_COOKIE_SECURE = _ssl_enabled
    CSRF_COOKIE_SECURE = _ssl_enabled
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if _ssl_enabled else None
else:
    # Explicitly disable for tests and debug mode
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_PROXY_SSL_HEADER = None

# Container Registry
CONTAINER_REGISTRY_URL = config(
    'CONTAINER_REGISTRY_URL',
    default='registry.smsly.cloud')
REGISTRY_USER = config('REGISTRY_USER', default='')
REGISTRY_PASSWORD = config('REGISTRY_PASSWORD', default='')
# Webhook secret: keep production running even if omitted.
_GITHUB_WEBHOOK_SECRET_RAW = str(config('GITHUB_WEBHOOK_SECRET', default='')).strip()
if IS_TESTING:
    GITHUB_WEBHOOK_SECRET = _GITHUB_WEBHOOK_SECRET_RAW or 'test-github-webhook-secret'
elif DEBUG:
    GITHUB_WEBHOOK_SECRET = _GITHUB_WEBHOOK_SECRET_RAW or 'replace_me_with_random_string'
else:
    if _GITHUB_WEBHOOK_SECRET_RAW:
        GITHUB_WEBHOOK_SECRET = _GITHUB_WEBHOOK_SECRET_RAW
    else:
        print("[settings] WARNING: GITHUB_WEBHOOK_SECRET missing; deriving fallback value from SECRET_KEY.")
        GITHUB_WEBHOOK_SECRET = f"{SECRET_KEY}-github-webhook"
# SECURITY: No wildcard default - prevents host header injection
# (DOMAIN moved to top of file)
_DEFAULT_TUNNEL_BASE_DOMAIN = 'tunnel.localhost'
if DOMAIN and DOMAIN != 'localhost':
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', DOMAIN):
        _DEFAULT_TUNNEL_BASE_DOMAIN = f'tunnel.{DOMAIN}.sslip.io'
    else:
        _DEFAULT_TUNNEL_BASE_DOMAIN = f'tunnel.{DOMAIN}'
TUNNEL_BASE_DOMAIN = (
    config('TUNNEL_DOMAIN', default=_DEFAULT_TUNNEL_BASE_DOMAIN)
    or _DEFAULT_TUNNEL_BASE_DOMAIN
).strip()
# Infrastructure Version Control
INFRA_VERSION = '2026.05.11.10.35'

ENABLE_LEGACY_TUNNEL_API = config(
    'ENABLE_LEGACY_TUNNEL_API',
    default=False,
    cast=bool,
)
# Restrictive ALLOWED_HOSTS with mandatory internal whitelisting.
_BASE_HOSTS = ['localhost', '127.0.0.1', 'backend', 'smsly-hosting-backend-1']
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default=','.join(_BASE_HOSTS), cast=Csv())
for host in _BASE_HOSTS:
    if host not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(host)

if DOMAIN and DOMAIN != 'localhost' and DOMAIN not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(DOMAIN)
# Ensure common cPanel/CloudNode hostnames are allowed for automated checks
ALLOWED_HOSTS.extend(['.cprapid.com', '.sslip.io'])
APPEND_SLASH = False

# ---------------------------------------------------------------------------
# Dynamically include the domain from PlatformConfig (DB) so that domain
# changes made via the Settings UI take effect after container restart,
# without requiring manual .env edits.
# ---------------------------------------------------------------------------
try:
    import dj_database_url
    from urllib.parse import urlparse
    import psycopg2
    
    # Manually extract DB credentials from the DATABASE_URL since Django apps aren't loaded yet.
    db_url = config('DATABASE_URL', default='')
    if db_url:
        parsed = urlparse(db_url)
        conn = psycopg2.connect(
            dbname=parsed.path.lstrip('/'),
            user=parsed.username,
            password=parsed.password,
            host=parsed.hostname,
            port=parsed.port
        )
        with conn.cursor() as cursor:
            cursor.execute("SELECT domain, use_ssl FROM deployments_platformconfig ORDER BY id ASC LIMIT 1;")
            row = cursor.fetchone()
            if row and row[0]:
                db_domain = str(row[0]).strip().lower().rstrip('.')
                if db_domain:
                    # Sync to ALLOWED_HOSTS
                    if db_domain not in ALLOWED_HOSTS:
                        ALLOWED_HOSTS.append(db_domain)
                    # Override DOMAIN in memory so that other settings depend on the DB state
                    DOMAIN = db_domain
                    # Sync USE_SSL from DB so security settings stay consistent
                    db_use_ssl = bool(row[1]) if len(row) > 1 else False
                    # Only override USE_SSL from DB if it was explicitly set
                    if len(row) > 1:
                        os.environ['USE_SSL'] = 'True' if db_use_ssl else 'False'
                    # Update SITE_URL to match the DB domain
                    _db_is_ip = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', db_domain))
                    _db_proto = 'https' if (db_use_ssl and not _db_is_ip) else 'http'
                    if not DEBUG:
                        SITE_URL = f"{_db_proto}://{db_domain}"
        conn.close()
except Exception as e:
    print(f"[settings] Could not sync PlatformConfig domain to memory on boot: {e}")


# ---------------------------------------------------------------------------
# SITE_URL and Protocol
# ---------------------------------------------------------------------------

# Finalize SITE_URL: Explicit .env > DB Sync > Fallback
_env_site_url = config('SITE_URL', default=None)
if _env_site_url:
    SITE_URL = _env_site_url
elif 'SITE_URL' not in locals():
    _use_ssl_site = _env_bool('USE_SSL', default='False')
    _is_ip_site = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', DOMAIN))
    _is_local_site = DOMAIN.lower() in ('localhost', '127.0.0.1')
    _proto_site = 'https' if (_use_ssl_site and not _is_ip_site and not _is_local_site) else 'http'
    SITE_URL = ('http://localhost:3000' if DEBUG else f'{_proto_site}://{DOMAIN}')

# GitHub OAuth Callback URL (explicit override to prevent redirect_uri mismatch)
# If set, this value is used verbatim for GitHub OAuth redirect_uri
# Format: https://your-domain.com/auth/github/callback
GITHUB_OAUTH_CALLBACK_URL = config('GITHUB_OAUTH_CALLBACK_URL', default=None)

# Stripe Billing (optional but required for paid plans)
STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET', default='')
STRIPE_PRICE_PRO = config('STRIPE_PRICE_PRO', default='')

# Billing (non-Stripe providers use one-time "period" activations)
BILLING_CURRENCY = config('BILLING_CURRENCY', default='USD')
BILLING_PRO_AMOUNT = config('BILLING_PRO_AMOUNT', default='29.00')
BILLING_PRO_PERIOD_DAYS = config('BILLING_PRO_PERIOD_DAYS', default=30, cast=int)

# Flutterwave (optional)
FLUTTERWAVE_SECRET_KEY = config('FLUTTERWAVE_SECRET_KEY', default='')
FLUTTERWAVE_PUBLIC_KEY = config('FLUTTERWAVE_PUBLIC_KEY', default='')
FLUTTERWAVE_WEBHOOK_SECRET_HASH = config('FLUTTERWAVE_WEBHOOK_SECRET_HASH', default='')

# Cryptomus (optional)
CRYPTOMUS_MERCHANT_ID = config('CRYPTOMUS_MERCHANT_ID', default='')
CRYPTOMUS_API_KEY = config('CRYPTOMUS_API_KEY', default='')


# Agent Mode Optimization: Prune apps and middleware to save RAM
IS_AGENT_MODE = os.environ.get('MODE') == 'agent'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.sites',  # Required for allauth
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third party
    'rest_framework',
    'corsheaders',
    'drf_spectacular',
    'django_celery_results',
    'encrypted_model_fields',
    'rest_framework.authtoken',
    'dj_rest_auth',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.github',
    'allauth.socialaccount.providers.google',

    # Local
    'apps.core',
    'apps.deployments',
    'apps.cloud',
    'apps.teams',
    'apps.billing',
    'apps.domains',
    'apps.intelligence',
    'apps.notifications',
    'apps.addons',
    'apps.autoscaler',
    'apps.licensing',
]

if IS_AGENT_MODE:
    # Remove Master-only apps to save memory on 2GB nodes
    APPS_TO_REMOVE = {
        'apps.billing',      # Nodes don't handle billing
        'apps.licensing',    # Nodes don't verify licenses (Master does)
        'apps.intelligence', # Heavy AI/NLP can be disabled on Lite nodes
        'django.contrib.admin', # Optional: Disable admin UI on nodes
    }
    INSTALLED_APPS = [app for app in INSTALLED_APPS if app not in APPS_TO_REMOVE]


AUTOSCALER_API_URL = os.environ.get('AUTOSCALER_API_URL', 'http://localhost:9876')
CADDY_CONFIG_DIR = os.environ.get("CADDY_CONFIG_DIR", "/caddy-config")

MIDDLEWARE = [
    'apps.deployments.middleware.DynamicAllowedHostsMiddleware', # Ensures multi-worker host sync
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.core.middleware.security.SecurityMiddleware',  # Zero Trust HMAC V2
    'apps.core.middleware.ratelimit.RateLimitMiddleware', # App-layer Rate Limiting
    'apps.licensing.middleware.TierLimitsMiddleware', # License Tier Enforcement
    'allauth.account.middleware.AccountMiddleware',
]

if IS_AGENT_MODE:
    # Prune Middleware to avoid Model Class registry errors (since apps are removed)
    MIDDLEWARE = [m for m in MIDDLEWARE if not m.startswith('apps.licensing')]


ROOT_URLCONF = 'config.urls'

SITE_ID = 1

# dj-rest-auth / allauth configuration
# Allow signing in with either username or email.
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
# Fix dj-rest-auth deprecation warnings
REST_AUTH = {
    'SIGNUP_FIELDS': {
        'username': {'required': True},
        'email': {'required': True},
    },
}

# Store social OAuth tokens (required for private-repo deploys via linked GitHub accounts).
# Explicitly set to avoid relying on allauth defaults.
SOCIALACCOUNT_STORE_TOKENS = True

# Redirect to frontend callback after login
LOGIN_REDIRECT_URL = '/auth/callback'
ACCOUNT_LOGOUT_REDIRECT_URL = '/login'

# ── Session cookie security ──────────────────────────────────────────────────
# 'Strict' is required for allauth v65's _redirect_strict_samesite() workaround:
# Chrome 145+ doesn't send SameSite=Lax cookies on cross-site redirect chains
# (GitHub→our callback). 'Strict' triggers allauth to do a self-redirect, making
# the final callback same-site so the browser sends the session cookie.
SESSION_COOKIE_SAMESITE = 'Strict'
SESSION_COOKIE_SECURE = not DEBUG and not IS_TESTING
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = not DEBUG and not IS_TESTING

# Ensure allauth uses HTTPS callback URLs in production (prevents CSRF Referer mismatch)
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'https' if not DEBUG and not IS_TESTING else 'http'

# Custom allauth adapters (callback redirect behavior)
ACCOUNT_ADAPTER = 'apps.deployments.adapters.CustomAccountAdapter'
SOCIALACCOUNT_ADAPTER = 'apps.deployments.adapters.CustomSocialAccountAdapter'

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

SOCIALACCOUNT_PROVIDERS = {
    'github': {
        'SCOPE': [
            'user',
            'repo',
            'read:org',
        ],
    },
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        }
    }
}

# allauth v65+: Skip the intermediate POST confirmation form on social login.
# Without this, GET /accounts/github/login/ renders a form instead of redirecting
# to GitHub, breaking the OAuth state flow and causing 401 on the callback.
SOCIALACCOUNT_LOGIN_ON_GET = True

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# SECURITY: Fail-fast in production — no dev-creds default
_fallback_sqlite_path = (BASE_DIR / 'fallback.db').resolve().as_posix()
_DATABASE_DEFAULT = (
    'postgres://postgres:postgres@localhost:5432/smsly_hosting'
    if DEBUG
    else f'sqlite:///{_fallback_sqlite_path}'
)
DATABASE_CONNECT_TIMEOUT = config('DATABASE_CONNECT_TIMEOUT', default=5, cast=int)
REDIS_SOCKET_TIMEOUT = config('REDIS_SOCKET_TIMEOUT', default=5, cast=int)
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default=_DATABASE_DEFAULT),
        # PgCat (transaction pooling) requires conn_max_age=0
        # so Django returns connections to the pool after each request.
        conn_max_age=0,
        conn_health_checks=True,
    )
}

# ---------------------------------------------------------------------------
# Migration/DDL Safety:
# PgCat in 'transaction' mode (default) does not support SET/SAVEPOINT.
# We derive a 'session' pool alias (smsly_hosting_session) or use a 'direct'
# connection to bypass the pooler during migrations.
# ---------------------------------------------------------------------------
_db_url = config('DATABASE_URL', default=_DATABASE_DEFAULT)
if _db_url and 'pgcat' in _db_url and '_session' not in _db_url:
    _session_url = _db_url.rstrip('/') + '_session'
    DATABASES['session'] = dj_database_url.config(
        default=_session_url,
        conn_max_age=0,
        conn_health_checks=True,
    )

# Direct connection for migrations — bypasses PgCat entirely if configured.
_DIRECT_DB_URL = config('DIRECT_DATABASE_URL', default='')
if _DIRECT_DB_URL:
    DATABASES['direct'] = dj_database_url.config(
        default=_DIRECT_DB_URL,
        conn_max_age=0,
        conn_health_checks=True,
    )

# Disable server-side cursors – incompatible with PgCat transaction pooling
DISABLE_SERVER_SIDE_CURSORS = True

# Apply PostgreSQL-specific settings to all configured databases
for _db_cfg in DATABASES.values():
    if 'postgresql' not in str(_db_cfg.get('ENGINE', '')):
        continue
    _db_cfg.setdefault('OPTIONS', {})
    _db_cfg['OPTIONS'].setdefault('connect_timeout', DATABASE_CONNECT_TIMEOUT)
    _db_cfg['DISABLE_SERVER_SIDE_CURSORS'] = True

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
MAX_UPLOAD_SIZE = config('MAX_UPLOAD_SIZE', default=100 * 1024 * 1024, cast=int)
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.deployments.api_token_auth.APITokenAuthentication',
        'apps.deployments.api_token_auth.RemoteSyncHMACAuthentication',
        'rest_framework.authentication.TokenAuthentication',
        # CSRF-exempt session auth: prevents 403 when token auth fails and
        # DRF falls through to session auth (which enforces CSRF by default).
        # API endpoints use token auth primarily; session is only a fallback.
        'apps.core.auth.CsrfExemptSessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '200/hour',
        'user': '5000/hour',
        'deployments': '10/hour',
        'deployment_burst': '3/minute',
    },
}
API_RATE_LIMIT = config('API_RATE_LIMIT', default=1000, cast=int)
API_RATE_LIMIT_FAIL_CLOSED = config(
    'API_RATE_LIMIT_FAIL_CLOSED',
    default=False,
    cast=bool,
)

# Celery
REDIS_PASSWORD = config('REDIS_PASSWORD', default='')
REDIS_HOST = config('REDIS_HOST', default='redis')
REDIS_PORT = config('REDIS_PORT', default='6379')
REDIS_SCHEME = config('REDIS_SCHEME', default='redis')

if REDIS_PASSWORD:
    _REDIS_BASE_URL = f"{REDIS_SCHEME}://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
else:
    _REDIS_BASE_URL = f"{REDIS_SCHEME}://{REDIS_HOST}:{REDIS_PORT}"

# Prefer explicit REDIS_URL override when provided; otherwise build from host/port.
# Prefer explicit CELERY_BROKER_URL override; otherwise build from user/pass.
_RABBITMQ_USER = config('RABBITMQ_DEFAULT_USER', default='smsly_user')
_RABBITMQ_PASS = config('RABBITMQ_PASSWORD', default='smsly_password')
_RABBITMQ_HOST = config('RABBITMQ_HOST', default='rabbitmq')
_RABBITMQ_PORT = config('RABBITMQ_PORT', default='5672')
_RABBITMQ_VHOST = config('RABBITMQ_DEFAULT_VHOST', default='')

_FALLBACK_BROKER_URL = f"amqp://{_RABBITMQ_USER}:{_RABBITMQ_PASS}@{_RABBITMQ_HOST}:{_RABBITMQ_PORT}/{_RABBITMQ_VHOST}"
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default=_FALLBACK_BROKER_URL)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = config(
    'CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP',
    default=True,
    cast=bool,
)
CELERY_TASK_ALWAYS_EAGER = IS_TESTING

# Django cache: use Redis (needed for accurate /health cache checks + rate limits).
# Use a dedicated DB index (2) to avoid colliding with Celery (0) / Channels (1).
def _redis_url_with_db(base_url: str, db: int) -> str:
    """Replace the DB number in a Redis URL (handles any DB suffix safely)."""
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(base_url)
    return urlunparse(parsed._replace(path=f'/{db}'))

REDIS_CACHE_URL = _redis_url_with_db(_REDIS_BASE_URL, 2)

if IS_TESTING:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "OPTIONS": {
                "socket_connect_timeout": REDIS_SOCKET_TIMEOUT,
                "socket_timeout": REDIS_SOCKET_TIMEOUT,
            },
        }
    }

# Channels (WebSockets) - use Redis so Celery tasks can broadcast logs/status to live UIs.
CHANNEL_REDIS_URL = config('REDIS_URL', default=f"{_REDIS_BASE_URL}/1")
if isinstance(CHANNEL_REDIS_URL, str) and CHANNEL_REDIS_URL.endswith('/0'):
    CHANNEL_REDIS_URL = CHANNEL_REDIS_URL[:-2] + '/1'

CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            'hosts': [CHANNEL_REDIS_URL],
        },
    },
}
CELERY_RESULT_BACKEND = 'django-db'

# Use separate queues for different task types
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_DEFAULT_EXCHANGE = 'celery'
CELERY_TASK_DEFAULT_ROUTING_KEY = 'celery'
CELERY_TASK_CREATE_MISSING_QUEUES = True

CELERY_QUEUES = {
    'celery': {
        'exchange': 'celery',
        'exchange_type': 'direct',
        'routing_key': 'celery',
    },
    'deploy': {
        'exchange': 'deploy',
        'exchange_type': 'direct',
        'routing_key': 'deploy',
    },
    'fast': {
        'exchange': 'fast',
        'exchange_type': 'direct',
        'routing_key': 'fast',
    },
}

CELERY_TASK_ROUTES = {
    'apps.deployments.tasks.smart_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks.resume_deploy_task': {'queue': 'deploy'},
    'apps.deployments.tasks.auto_promote_task': {'queue': 'deploy'},
    'apps.deployments.tasks.promote_deployment_task': {'queue': 'deploy'},
    'apps.deployments.tasks.provision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.deprovision_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.backup_addon_task': {'queue': 'deploy'},
    'apps.deployments.tasks.restore_addon_task': {'queue': 'deploy'},
}

# Allow heavy Docker builds (e.g. torch, playwright) up to 2 hours
CELERY_TASK_SOFT_TIME_LIMIT = 7200  # 2 hours
CELERY_TASK_TIME_LIMIT = 7500       # 2h 5m hard kill
# NOTE: Beat schedule is defined in config/celery.py (the authoritative source)

# CORS - allow "*" only in DEBUG when explicitly enabled.
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = bool(
    DEBUG and config('CORS_ALLOW_ALL_ORIGINS', default=False, cast=bool)
)
CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default=f'http://localhost:3000,{SITE_URL}',
    cast=Csv())
# ZH-007 FIX: No wildcard subdomains — explicit trusted origins only
# Include both the frontend SITE_URL and the backend's own domain for OAuth callbacks
_BACKEND_ORIGIN = f'https://{DOMAIN}' if not DEBUG else 'http://localhost:8000'
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default=f'{SITE_URL},{_BACKEND_ORIGIN}',
    cast=Csv())
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# =============================================================================
# Structured Logging (JSON format for production)
# =============================================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json' if not DEBUG else 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': config('DJANGO_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
        'apps.deployments': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}

# =============================================================================
# Sentry (optional)
# =============================================================================
SENTRY_DSN = config('SENTRY_DSN', default='')
SENTRY_TRACES_SAMPLE_RATE = config('SENTRY_TRACES_SAMPLE_RATE', default=0.0, cast=float)
SENTRY_PROFILES_SAMPLE_RATE = config('SENTRY_PROFILES_SAMPLE_RATE', default=0.0, cast=float)
SENTRY_ENVIRONMENT = config('SENTRY_ENVIRONMENT', default=('development' if DEBUG else 'production'))
SENTRY_RELEASE = config('SENTRY_RELEASE', default='')

if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.redis import RedisIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
            environment=SENTRY_ENVIRONMENT,
            release=SENTRY_RELEASE or None,
            send_default_pii=False,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=SENTRY_PROFILES_SAMPLE_RATE,
        )
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("Sentry init failed: %s", e)

# =============================================================================
# Email Configuration (SMTP)
# =============================================================================
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST', default='localhost')
EMAIL_PORT = config('EMAIL_PORT', default=25, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=False, cast=bool)
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=False, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
EMAIL_TIMEOUT = config('EMAIL_TIMEOUT', default=10, cast=int)

# Use noreply@{DOMAIN} if DEFAULT_FROM_EMAIL is not set in env
_DEFAULT_FROM = f"noreply@{DOMAIN}" if DOMAIN != 'localhost' else 'noreply@smsly.cloud'
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=_DEFAULT_FROM)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# (Patching moved higher up)
