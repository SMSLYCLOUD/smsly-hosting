import re

_SECRET_KEY_NAMES = frozenset({
    'SECRET_KEY', 'JWT_SECRET', 'JWT_SIGNING_KEY',
    'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET',
    'GITHUB_TOKEN', 'GITLAB_TOKEN', 'BITBUCKET_TOKEN',
    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
    'FIELD_ENCRYPTION_KEY', 'BACKUP_ENCRYPTION_KEY',
    'GATEWAY_SECRET', 'WEBHOOK_SECRET', 'OAUTH_CLIENT_SECRET',
    'INTERNAL_API_TOKEN', 'GITLAB_SECRET_TOKEN', 'GITHUB_WEBHOOK_SECRET',
    'BITBUCKET_WEBHOOK_SECRET', 'CLOUDFLARE_API_TOKEN', 'SENTRY_DSN',
    'SMTP_PASSWORD', 'PASSWORD',
    'DATABASE_URL', 'POSTGRES_URL', 'POSTGRES_PASSWORD',
    'REDIS_URL', 'RABBITMQ_URL', 'AMQP_URL', 'BROKER_URL',
    'CELERY_BROKER_URL', 'CELERY_RESULT_BACKEND', 'CACHE_URL', 'RATE_LIMIT_REDIS_URL',
    'API_KEY', 'API_TOKEN', 'TOKEN', 'AUTH_TOKEN', 'ACCESS_TOKEN', 'REFRESH_TOKEN',
})


def redact_secrets(text: str) -> str:
    if not text:
        return text
    for key in _SECRET_KEY_NAMES:
        text = re.sub(
            rf'({re.escape(key)}\s*=\s*)([^\s"\'&]+)',
            r'\1[REDACTED]',
            text,
            flags=re.IGNORECASE,
        )
    text = re.sub(r'(://[^:/\s]+:)([^@\s]+)(@)', r'\1[REDACTED]\3', text)
    text = re.sub(r'(?i)((?:Authorization|X-API-Key|X-Auth-Token):\s*)(\S+)', r'\1[REDACTED]', text)
    return text
