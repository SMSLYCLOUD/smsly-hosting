"""
Utility functions for deployment tasks.
"""
import json
import logging
import os
import re
import secrets
import tarfile
import tempfile
import hashlib
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def log_event(action: str, target: str = 'none', actor: str = 'system', metadata: dict = None):
    """
    Exhaustive Audit Logging helper.
    Ensures consistent detailed event capture across the platform.
    """
    from apps.deployments.models_audit import AuditLog
    try:
        # Standardise metadata with common fields if not present
        meta = metadata or {}
        if 'timestamp' not in meta:
            meta['timestamp'] = timezone.now().isoformat()
            
        return AuditLog.objects.create(
            actor=actor,
            action=action,
            target=target,
            metadata=meta
        )
    except Exception as e:
        logger.error(f"AuditLog creation failed: {e}")
        return None


# ── Resource size heuristics (by dependency weight) ─────────────────────
# Maps heavy Python packages to recommended minimum resources.
_HEAVY_DEPS = {
    'torch': (1.0, 2048), 'pytorch': (1.0, 2048),
    'tensorflow': (1.0, 2048), 'transformers': (1.0, 1536),
    'playwright': (0.5, 1024), 'selenium': (0.5, 1024),
    'pandas': (0.5, 1024), 'numpy': (0.5, 768),
    'scipy': (0.5, 1024), 'scikit-learn': (0.5, 1024),
    'opencv-python': (0.5, 1024), 'pillow': (0.25, 768),
    'spacy': (0.5, 1024), 'celery': (0.25, 768),
}


def parse_ai_resource_recommendation(ai_response: str) -> dict:
    """
    Extract structured JSON from an AI analysis response.

    Handles responses that may include markdown code blocks.
    Returns a safe dict with keys: resources, required_env_vars, issues, diagnosis.
    Returns empty dict on parse failure (non-blocking).
    """
    if not ai_response:
        return {}

    try:
        # Try to find JSON inside ```json ... ``` blocks first
        json_match = re.search(
            r'```(?:json)?\s*\n?(.*?)\n?\s*```',
            ai_response,
            re.DOTALL
        )
        if json_match:
            raw = json_match.group(1).strip()
        else:
            # Try to find a raw JSON object
            brace_match = re.search(r'\{.*\}', ai_response, re.DOTALL)
            if brace_match:
                raw = brace_match.group(0)
            else:
                return {}

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}

        # Validate and sanitise
        result = {}

        # Resources
        res = parsed.get('resources', {})
        if isinstance(res, dict):
            cpu = res.get('cpu_cores')
            mem = res.get('memory_mb')
            if cpu is not None or mem is not None:
                result['resources'] = {}
                if isinstance(cpu, (int, float)) and 0.1 <= cpu <= 16:
                    result['resources']['cpu_cores'] = round(float(cpu), 2)
                if isinstance(mem, (int, float)) and 128 <= mem <= 32768:
                    result['resources']['memory_mb'] = int(mem)

        # Required env vars
        env = parsed.get('required_env_vars', {})
        if isinstance(env, dict):
            result['required_env_vars'] = {
                str(k): str(v) for k, v in env.items()
                if isinstance(k, str) and k.strip()
            }

        # Issues (list of strings)
        issues = parsed.get('issues', [])
        if isinstance(issues, list):
            result['issues'] = [str(i) for i in issues[:10]]

        # Diagnosis (free text)
        diag = parsed.get('diagnosis', '')
        if isinstance(diag, str):
            result['diagnosis'] = diag[:5000]

        return result

    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.debug("Failed to parse AI resource recommendation: %s", e)
        return {}


def estimate_resources_from_deps(source_dir: str) -> dict:
    """
    Quick heuristic: scan requirements.txt / package.json for heavy deps
    and return recommended minimum resources.

    Returns: {'cpu_cores': float, 'memory_mb': int} or empty dict.
    """
    max_cpu = 0.0
    max_mem = 0

    # Python deps
    for req_file in ('requirements.txt', 'requirements/base.txt',
                     'requirements/production.txt'):
        path = os.path.join(source_dir, req_file)
        if os.path.isfile(path):
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        pkg = (line.strip().split('==')[0].split('>=')[0]
                               .split('<=')[0].split('[')[0].split('#')[0]
                               .strip().lower())
                        if pkg in _HEAVY_DEPS:
                            cpu, mem = _HEAVY_DEPS[pkg]
                            max_cpu = max(max_cpu, cpu)
                            max_mem = max(max_mem, mem)
            except OSError:
                pass

    if max_cpu > 0 or max_mem > 0:
        return {'cpu_cores': max_cpu, 'memory_mb': max_mem}
    return {}


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
    Automatically refreshes expired tokens using the stored refresh token.
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
    if not token:
        return None

    access_token = getattr(token, "token", None)
    if not access_token:
        return None

    # Check if token is expired and attempt refresh
    try:
        from django.utils import timezone
        expires_at = getattr(token, "expires_at", None)
        if expires_at and expires_at <= timezone.now():
            # Token expired - try to refresh
            refresh_token = getattr(token, "token_secret", None)
            if refresh_token:
                try:
                    from allauth.socialaccount.models import SocialApp
                    import requests as http_requests
                    from datetime import timedelta

                    app = SocialApp.objects.filter(provider="github").first()
                    if app:
                        resp = http_requests.post(
                            "https://github.com/login/oauth/access_token",
                            headers={"Accept": "application/json"},
                            data={
                                "client_id": app.client_id,
                                "client_secret": app.secret,
                                "grant_type": "refresh_token",
                                "refresh_token": refresh_token,
                            },
                            timeout=10,
                        )
                        data = resp.json()
                        if "access_token" in data:
                            token.token = data["access_token"]
                            if data.get("refresh_token"):
                                token.token_secret = data["refresh_token"]
                            expires_in = data.get("expires_in", 28800)
                            token.expires_at = timezone.now() + timedelta(seconds=int(expires_in))
                            token.save()
                            access_token = token.token
                            logger.info("GitHub OAuth token refreshed for user %s", user)
                        else:
                            logger.warning("GitHub token refresh failed: %s", data.get("error_description", data))
                except Exception as exc:
                    logger.warning("GitHub token refresh error: %s", exc)
            else:
                logger.warning("No refresh token available for user %s - reconnect required", user)
    except Exception as exc:
        logger.warning("Token expiry check failed: %s", exc)

    return access_token or None


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


import re

def append_log(deployment, log_line):
    """
    Append logs safely using refresh and update_fields.
    Strips NUL (\x00) characters to prevent PostgreSQL text field errors.
    """
    if not log_line:
        return

    import uuid
    from django.utils import timezone
    correlation_id = getattr(deployment, "_deploy_correlation_id", None)
    if not correlation_id:
        correlation_id = str(uuid.uuid4())[:8]
        deployment._deploy_correlation_id = correlation_id
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Sanitize for PostgreSQL (NUL bytes are not allowed in text/varchar)
    sanitized_log = str(log_line).replace('\x00', '')
    sanitized_log = f"[{timestamp}] [tx:{correlation_id}] {sanitized_log}"

    sanitized_log = re.sub(r"(redis(?:s)?://(?:[^:]*:)?)[^@]+(@)", r"\1***\2", sanitized_log)
    sanitized_log = re.sub(r"(postgres(?:ql)?://(?:[^:]*:)?)[^@]+(@)", r"\1***\2", sanitized_log)
    sanitized_log = re.sub(r"(?i)(password|secret|token|api_key)=([^\s&]+)", r"\1=***", sanitized_log)

    # 2. Refresh logs to avoid overwrite race conditions
    deployment.refresh_from_db(fields=['build_logs'])
    deployment.build_logs += sanitized_log
    deployment.save(update_fields=['build_logs'])
    broadcast_log(deployment, sanitized_log)


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


def get_source_root_dir() -> str:
    """Detect local source root relative to the codebase."""
    # utils.py is in apps/deployments/
    # target: / (project root)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def build_local_source_bundle() -> str:
    """
    Build a temporary tar.gz bundle of source code for tokenless provisioning.
    Returns local temporary file path.
    """
    source_root = get_source_root_dir()
    if not os.path.isdir(source_root):
        raise FileNotFoundError(f"Source root not found: {source_root}")

    fd, archive_path = tempfile.mkstemp(prefix="smsly-src-", suffix=".tar.gz")
    os.close(fd)

    excluded = {
        ".git",
        "node_modules",
        ".next",
        "__pycache__",
        ".venv",
        "venv",
        ".env",
        ".credentials",
        ".git-credentials",
        "backups",
        "scratch",
        "media",
    }

    with tarfile.open(archive_path, mode="w:gz") as tar:
        for root, dirs, files in os.walk(source_root, topdown=True):
            dirs[:] = [d for d in dirs if d not in excluded]
            rel_root = os.path.relpath(root, source_root)
            rel_root = "" if rel_root == "." else rel_root

            for filename in files:
                if filename in excluded:
                    continue
                local_path = os.path.join(root, filename)
                rel_path = os.path.join(rel_root, filename) if rel_root else filename
                try:
                    tar.add(local_path, arcname=rel_path, recursive=False)
                except (PermissionError, FileNotFoundError, OSError):
                    # Skip unreadable/transient files in host-mounted source root.
                    continue

    return archive_path


def resolve_running_container(service, deployment=None):
    """
    Resolve a running Docker container for a service.

    Priority:
    1. Try the deployment's stored container_id (fast path).
    2. Fall back to searching running containers by ``smsly.service_id`` label.
    3. Fall back to searching running containers by name matching service name.

    Returns a docker Container object, or None if no running container is found.
    """
    from apps.cloud.docker_client import get_docker_client
    from apps.deployments.models import Deployment

    if deployment is None:
        deployment = service.deployments.filter(status='ACTIVE').order_by('-created_at').first()
    if not deployment:
        return None

    client = get_docker_client()
    container_id = (deployment.container_id or "").strip()
    service_id = str(service.pk)

    # Priority 1: Try deployment's stored container_id
    if container_id:
        try:
            container = client.containers.get(container_id)
            if container.status == 'running':
                return container
        except Exception:
            pass

    # Priority 2: Search by smsly.service_id label
    try:
        containers = client.containers.list(
            filters={'label': f'smsly.service_id={service_id}', 'status': 'running'},
        )
        if containers:
            return containers[0]
    except Exception:
        pass

    # Priority 3: Search by container name matching the service name
    try:
        containers = client.containers.list(
            filters={'name': service.name, 'status': 'running'},
        )
        if containers:
            return containers[0]
    except Exception:
        pass

    return None
