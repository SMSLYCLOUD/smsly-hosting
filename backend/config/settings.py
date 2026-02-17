"""Settings module."""
import os
from pathlib import Path
from decouple import config, Csv
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY: No default - service MUST crash if SECRET_KEY is missing
# Generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
SECRET_KEY = config('SECRET_KEY')
FIELD_ENCRYPTION_KEY = config('FIELD_ENCRYPTION_KEY')

# Validate encryption key format (Fernet requirement: 32 bytes, URL-safe base64)
try:
    from cryptography.fernet import Fernet
    Fernet(FIELD_ENCRYPTION_KEY.encode() if isinstance(FIELD_ENCRYPTION_KEY, str) else FIELD_ENCRYPTION_KEY)
except Exception as e:
    raise ValueError(f"Invalid FIELD_ENCRYPTION_KEY: {e}. Generate with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'") from e
DEBUG = config('DEBUG', default=False, cast=bool)
SMSLY_DISABLE_SIGNATURE_CHECK = config('SMSLY_DISABLE_SIGNATURE_CHECK', default=False, cast=bool)

# Security hardening
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = config('USE_SSL', default=False, cast=bool)
    SESSION_COOKIE_SECURE = config('USE_SSL', default=False, cast=bool)
    CSRF_COOKIE_SECURE = config('USE_SSL', default=False, cast=bool)
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Container Registry
CONTAINER_REGISTRY_URL = config(
    'CONTAINER_REGISTRY_URL',
    default='registry.smsly.cloud')
REGISTRY_USER = config('REGISTRY_USER', default='')
REGISTRY_PASSWORD = config('REGISTRY_PASSWORD', default='')
# ZH-010 FIX: Webhook secret MUST be set in production (fail-closed)
if DEBUG:
    GITHUB_WEBHOOK_SECRET = config('GITHUB_WEBHOOK_SECRET', default='')
else:
    GITHUB_WEBHOOK_SECRET = config('GITHUB_WEBHOOK_SECRET')  # crash if missing
# SECURITY: No wildcard default - prevents host header injection
DOMAIN = (config('DOMAIN', default='localhost') or 'localhost').strip()
_ALLOWED_HOSTS_DEFAULT = f'localhost,127.0.0.1,{DOMAIN}' if DOMAIN else 'localhost,127.0.0.1'
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default=_ALLOWED_HOSTS_DEFAULT, cast=Csv())

SITE_URL = config(
    'SITE_URL',
    # NOTE: Used for OAuth/billing redirects. Override in env if you terminate TLS elsewhere.
    default=('http://localhost:3000' if DEBUG else f'https://{DOMAIN}')
)

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
]

MIDDLEWARE = [
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
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'config.urls'

SITE_ID = 1

# dj-rest-auth / allauth configuration
# Allow signing in with either username or email.
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']

# Store social OAuth tokens (required for private-repo deploys via linked GitHub accounts).
# Explicitly set to avoid relying on allauth defaults.
SOCIALACCOUNT_STORE_TOKENS = True

# Redirect to frontend callback after login
LOGIN_REDIRECT_URL = '/auth/callback'
ACCOUNT_LOGOUT_REDIRECT_URL = '/login'

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
_DATABASE_DEFAULT = 'postgres://postgres:postgres@localhost:5432/smsly_hosting' if DEBUG else ''
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default=_DATABASE_DEFAULT),
        conn_max_age=600
    )
}
if not DEBUG and not DATABASES['default'].get('NAME'):
    raise ValueError("DATABASE_URL must be set in production (DEBUG=False)")

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
        'rest_framework.authentication.TokenAuthentication',
        # Keep session auth as a fallback (used by /api/v1/auth/session-token/ after OAuth redirects),
        # but prefer token auth to avoid CSRF failures when both session cookies and Authorization
        # headers are present.
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
        'deployments': '10/hour',
        'deployment_burst': '3/minute',
    },
}

# Celery
REDIS_PASSWORD = config('REDIS_PASSWORD', default='')
REDIS_HOST = config('REDIS_HOST', default='redis')
REDIS_PORT = config('REDIS_PORT', default='6379')

if REDIS_PASSWORD:
    _REDIS_BASE_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}"
    CELERY_BROKER_URL = f"{_REDIS_BASE_URL}/0"
else:
    CELERY_BROKER_URL = config('REDIS_URL', default='redis://localhost:6379/0')

# Django cache: use Redis (needed for accurate /health cache checks + rate limits).
# Use a dedicated DB index to avoid colliding with Celery/Channels.
REDIS_CACHE_URL = (
    f"{_REDIS_BASE_URL}/2" if REDIS_PASSWORD else (
        CELERY_BROKER_URL[:-2] + "/2" if isinstance(CELERY_BROKER_URL, str) and CELERY_BROKER_URL.endswith("/0")
        else CELERY_BROKER_URL
    )
)

if os.environ.get('TESTING'):
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
        }
    }

# Channels (WebSockets) - use Redis so Celery tasks can broadcast logs/status to live UIs.
CHANNEL_REDIS_URL = CELERY_BROKER_URL
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
# Allow heavy Docker builds (e.g. torch, playwright) up to 2 hours
CELERY_TASK_SOFT_TIME_LIMIT = 7200  # 2 hours
CELERY_TASK_TIME_LIMIT = 7500       # 2h 5m hard kill
# NOTE: Beat schedule is defined in config/celery.py (the authoritative source)

# CORS - ZH-006 FIX: Never allow all origins. Hardcoded to False.
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default=f'http://localhost:3000,{SITE_URL}',
    cast=Csv())
# ZH-007 FIX: No wildcard subdomains — explicit trusted origins only
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default=f'{SITE_URL}',
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
