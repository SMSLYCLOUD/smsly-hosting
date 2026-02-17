"""
Utility functions for deployment tasks.
"""
import logging
import os
import re
import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def extract_dockerfile_arg_names(dockerfile_path: str) -> set[str]:
    """
    Extract build-arg names declared via `ARG ...` in a Dockerfile.
    """
    arg_names: set[str] = set()
    try:
        with open(dockerfile_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if not line.upper().startswith("ARG "):
                    continue
                # Syntax: ARG name[=default]
                arg_def = line[4:].strip()
                if not arg_def:
                    continue
                name = arg_def.split("=", 1)[0].strip()
                name = name.split()[0].strip()
                if name:
                    arg_names.add(name)
    except Exception:
        return set()
    return arg_names


def redact_values(text: str, values: list[str]) -> str:
    """Best-effort log redaction for secret values."""
    if not text:
        return text

    redacted = text
    for val in values:
        if not val:
            continue
        if len(val) < 4:
            continue
        redacted = redacted.replace(val, "***")

    redacted = re.sub(
        r"(--build-arg\s+(?:[A-Z0-9_]*?(?:SECRET|TOKEN|PASSWORD|KEY|DSN)[A-Z0-9_]*?)=)([^\s]+)",
        r"\1***",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def get_github_oauth_token_for_user(user):
    """
    Return the linked GitHub OAuth token for the given user (if connected).
    """
    if not user:
        return None

    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
    except Exception:
        return None

    account = (
        SocialAccount.objects.filter(user=user, provider="github")
        .order_by("-id")
        .first()
    )
    if not account:
        return None

    token = (
        SocialToken.objects.filter(account=account)
        .order_by("-id")
        .first()
    )
    return getattr(token, "token", None) or None


def broadcast_log(deployment, log_line):
    """
    Append log line to deployment and broadcast via WebSocket channel layer.
    Safe to call from sync Celery tasks.
    """
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'build_log',
                    'log': log_line,
                    'status': deployment.status,
                    'timestamp': timezone.now().isoformat(),
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast log: %s", e)


def broadcast_status(deployment):
    """Broadcast deployment status change via WebSocket."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'status_change',
                    'status': deployment.status,
                    'finished_at': (
                        deployment.finished_at.isoformat()
                        if deployment.finished_at else ''
                    ),
                    'duration_seconds': deployment.duration_seconds,
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast status: %s", e)


def broadcast_pipeline(deployment):
    """Broadcast pipeline stages update via WebSocket."""
    try:
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'pipeline_update',
                    'stages': deployment.pipeline_stages,
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast pipeline: %s", e)


def update_stage(deployment, name, status, duration=None):
    """Update a pipeline stage status."""
    # Always refresh to get latest stages state to avoid overwrites
    deployment.refresh_from_db(fields=['pipeline_stages'])
    stages = deployment.pipeline_stages or []
    if not isinstance(stages, list):
        stages = []

    found = False
    for stage in stages:
        if stage.get('name') == name:
            stage['status'] = status
            if duration is not None:
                stage['duration'] = duration
            found = True
            break

    if not found:
        stages.append({
            'name': name,
            'status': status,
            'duration': duration or 0
        })

    deployment.pipeline_stages = stages
    deployment.save(update_fields=['pipeline_stages'])
    broadcast_pipeline(deployment)


def append_log(deployment, log_line):
    """
    Append logs safely using refresh and update_fields.
    """
    if not log_line:
        return
    # Refresh logs to avoid overwrite race conditions
    deployment.refresh_from_db(fields=['build_logs'])
    deployment.build_logs += log_line
    deployment.save(update_fields=['build_logs'])
    broadcast_log(deployment, log_line)


def get_default_env_value(key: str, scan_result: dict, service_name: str) -> tuple[str | None, bool]:
    """
    Determine default value and injection status for an env var key.
    Returns (value, should_inject).
    """
    key_upper = key.upper()

    if 'SECRET_KEY' in key_upper or key_upper == 'SECRET':
        return secrets.token_urlsafe(50), True

    if key_upper in ('JWT_SECRET', 'SESSION_SECRET', 'COOKIE_SECRET', 'CSRF_SECRET', 'SIGNING_KEY', 'HASH_SALT'):
        return secrets.token_urlsafe(32), True

    if key_upper in ('DATABASE_URL', 'DB_URL', 'DB_URI', 'SQLALCHEMY_DATABASE_URI', 'SQLALCHEMY_DATABASE_URL'):
        stack = scan_result.get('stack', '')
        deps = scan_result.get('dependencies', [])
        dep_str = ' '.join(deps) if isinstance(deps, list) else str(deps)
        if 'asyncpg' in dep_str or 'async' in dep_str.lower():
            return 'postgresql+asyncpg://user:password@db:5432/dbname', True
        return 'postgresql://user:password@db:5432/dbname', True

    if key_upper == 'REDIS_URL':
        return 'redis://redis:6379/0', True

    if key_upper in ('CELERY_BROKER_URL', 'BROKER_URL'):
        return 'redis://redis:6379/1', True

    if key_upper in ('CELERY_RESULT_BACKEND', 'RESULT_BACKEND'):
        return 'redis://redis:6379/2', True

    if key_upper in ('MONGODB_URI', 'MONGO_URI', 'MONGO_URL'):
        return 'mongodb://mongo:27017/dbname', True

    if key_upper == 'PORT':
        return '8000', True

    if key_upper.endswith('_PORT'):
        return '8080', True

    if key_upper.endswith('_HOST') or key_upper.endswith('_HOSTNAME'):
        return 'localhost', True

    if key_upper in ('POSTGRES_USER', 'DB_USER'):
        return 'appuser', True

    if key_upper in ('POSTGRES_DB', 'DB_NAME'):
        return service_name.replace('-', '_')[:30], True

    if key_upper in ('POSTGRES_PASSWORD', 'DB_PASSWORD'):
        return secrets.token_urlsafe(24), True

    if key_upper in ('DEBUG', 'TESTING'):
        return 'false', True

    if key_upper in ('NODE_ENV', 'ENVIRONMENT'):
        return 'production', True

    if key_upper in ('ALLOWED_HOSTS', 'CORS_ALLOWED_ORIGINS'):
        return '*', True

    if key_upper in ('LOG_LEVEL',):
        return 'info', True

    if key_upper in ('WORKERS', 'WEB_CONCURRENCY'):
        return '4', True

    return None, False
