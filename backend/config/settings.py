import os
from pathlib import Path
from decouple import config, Csv, UndefinedValueError
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# SECURITY: Environment Detection
# =============================================================================
ENVIRONMENT = config('ENVIRONMENT', default='development').lower()
_is_production = ENVIRONMENT in ('production', 'staging', 'prod')

# =============================================================================
# SECURITY: Fail-fast for secrets in production
# =============================================================================
try:
    SECRET_KEY = config('SECRET_KEY')
except UndefinedValueError:
    if _is_production:
        raise RuntimeError("FATAL: SECRET_KEY is required in production!")
    SECRET_KEY = 'django-insecure-dev-only-key-never-use-in-production'

try:
    FIELD_ENCRYPTION_KEY = config('FIELD_ENCRYPTION_KEY')
except UndefinedValueError:
    if _is_production:
        raise RuntimeError("FATAL: FIELD_ENCRYPTION_KEY is required in production!")
    FIELD_ENCRYPTION_KEY = None  # Will fail at runtime if encryption is used

DEBUG = config('DEBUG', default=False, cast=bool)

# SECURITY: Prevent DEBUG=True in production
if _is_production and DEBUG:
    raise RuntimeError("FATAL: DEBUG=True is not allowed in production!")

# Security hardening (always enabled in non-DEBUG mode)
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Container Registry
CONTAINER_REGISTRY_URL = config('CONTAINER_REGISTRY_URL', default='registry.smsly.cloud')
REGISTRY_USER = config('REGISTRY_USER', default='')
REGISTRY_PASSWORD = config('REGISTRY_PASSWORD', default='')

# SECURITY: ALLOWED_HOSTS must be explicit in production
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*', cast=Csv())
if _is_production and '*' in ALLOWED_HOSTS:
    raise RuntimeError("FATAL: ALLOWED_HOSTS='*' is not allowed in production!")

CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='https://*.railway.app', cast=Csv())

INSTALLED_APPS = [
    'daphne',
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
    'apps.deployments',
    'apps.cloud',
    'apps.intelligence',
    'apps.billing',
    'apps.teams',
    'apps.domains',
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
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'config.urls'

SITE_ID = 1

# Redirect to frontend callback after login
LOGIN_REDIRECT_URL = '/auth/callback'
ACCOUNT_LOGOUT_REDIRECT_URL = '/login'

# Custom Adapters to inject Token into redirect URL
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

DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default='postgres://postgres:postgres@localhost:5432/smsly_hosting'),
        conn_max_age=600
    )
}

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
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.TokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# Celery
CELERY_BROKER_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = 'django-db'
CELERY_BEAT_SCHEDULE = {
    'collect-metrics-every-5-minutes': {
        'task': 'apps.deployments.tasks_metrics.collect_metrics_task',
        'schedule': 300.0, # 5 minutes
    },
}

# CORS - Use allowlist in production
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = config('CORS_ALLOW_ALL', default=False, cast=bool)
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000,https://smsly-hosting.com,http://209.159.155.100', cast=Csv())
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='https://*.railway.app,https://smsly-hosting.com,http://209.159.155.100', cast=Csv())
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
