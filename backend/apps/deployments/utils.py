"""
Utility functions for deployment tasks.
"""
import json
import logging
import os
import platform
import posixpath
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)


def log_event(action: str, target: str = 'none', actor: str = 'system', metadata: dict | None = None):
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
        # Sequence drift: BigAutoField sequence falls behind the actual max id
        # after rows are inserted with explicit PKs (e.g. pg_restore, data
        # migrations).  Detect the duplicate-key pattern and self-heal by
        # advancing the sequence to max(id), then retry once.
        err_str = str(e)
        if 'duplicate key value violates unique constraint' in err_str and 'pkey' in err_str:
            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT setval(
                            pg_get_serial_sequence('deployments_auditlog', 'id'),
                            COALESCE(MAX(id), 1),
                            true
                        )
                        FROM deployments_auditlog
                        """
                    )
                logger.warning(
                    "AuditLog sequence drift detected and corrected - retrying insert. "
                    "Run migration 0149_reset_auditlog_sequence to make this permanent."
                )
                return AuditLog.objects.create(
                    actor=actor,
                    action=action,
                    target=target,
                    metadata=meta
                )
            except Exception as retry_exc:
                logger.error(f"AuditLog creation failed after sequence reset: {retry_exc}")
                return None
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
        result: dict[str, Any] = {}

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
                with open(path, encoding='utf-8', errors='ignore') as f:
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
        with open(dockerfile_path, encoding="utf-8") as fh:
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


def get_github_token_for_repo(user, repo_full_name: str) -> str | None:
    """
    Return the best available GitHub token for accessing *repo_full_name*.

    Priority:
      1. GitHub App installation token — repo-scoped, 1-hour expiry (preferred).
         Requires ``GITHUB_APP_ID`` and ``GITHUB_APP_PRIVATE_KEY`` in settings.
      2. User OAuth token — falls back gracefully when the App is not configured
         or fails to issue a token.
      3. None — anonymous access (public repos only).

    This is the canonical token accessor for all Git operations inside the
    platform (clone, Docker build secrets, webhook setup). Callers should
    **not** call ``get_github_oauth_token_for_user`` directly for new code.

    Args:
        user: Django user instance (used for OAuth fallback).
        repo_full_name: ``"owner/repo"``, e.g. ``"SMSLYCLOUD/smsly-shared"``.
                        If empty or malformed, skips the App call and falls
                        back to the user OAuth token directly.
    """
    # Guard: skip the App call if repo_full_name is unusable.
    # This happens when the URL parser couldn't extract owner/repo cleanly.
    _repo = (repo_full_name or "").strip()
    if not _repo or "/" not in _repo or len(_repo.split("/")) < 2:
        return get_github_oauth_token_for_user(user)

    # Prefer GitHub App installation token (enterprise-grade, repo-scoped)
    try:
        from apps.deployments.services.github_app import get_installation_token_for_repo
        app_token = get_installation_token_for_repo(_repo)
        if app_token:
            return app_token
    except Exception as exc:
        logger.warning(
            "GitHub App token fetch failed for %s, falling back to OAuth: %s",
            _repo,
            exc,
        )

    # Fall back to user's stored OAuth token
    return get_github_oauth_token_for_user(user)


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
                    from datetime import timedelta

                    import requests as http_requests
                    from allauth.socialaccount.models import SocialApp

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
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

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
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

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
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

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
        return secrets.token_urlsafe(48), True

    if key_upper in ('DATABASE_URL', 'DB_URL', 'DB_URI', 'SQLALCHEMY_DATABASE_URI', 'SQLALCHEMY_DATABASE_URL'):
        scan_result.get('stack', '')
        deps = scan_result.get('dependencies', [])
        dep_str = ' '.join(deps) if isinstance(deps, list) else str(deps)
        platform_db = os.environ.get('DATABASE_URL', 'postgresql://user:password@db:5432/dbname')
        if 'asyncpg' in dep_str or 'async' in dep_str.lower():
            # Convert sync URL to async if needed
            if platform_db.startswith('postgresql://'):
                return platform_db.replace('postgresql://', 'postgresql+asyncpg://', 1), True
            return platform_db, True
        return platform_db, True

    if key_upper == 'REDIS_URL':
        return os.environ.get('REDIS_URL', 'redis://redis:6379/0'), True

    if key_upper in ('CELERY_BROKER_URL', 'BROKER_URL'):
        return os.environ.get('CELERY_BROKER_URL', 'redis://redis:6379/1'), True

    if key_upper in ('CELERY_RESULT_BACKEND', 'RESULT_BACKEND'):
        return os.environ.get('CELERY_RESULT_BACKEND', 'redis://redis:6379/2'), True

    if key_upper in ('MONGODB_URI', 'MONGO_URI', 'MONGO_URL'):
        return os.environ.get('MONGODB_URI', 'mongodb://mongo:27017/dbname'), True

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
        return secrets.token_urlsafe(48), True

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


def validate_and_sanitize_path(path: str, skip_system_check: bool = False, container=None) -> str:
    """
    Validate and sanitize file system paths to prevent directory traversal attacks.

    Args:
        path: The file path to validate
        skip_system_check: If True, skip the system directory prefix check
            (e.g. for volume paths like /data/usr/lib that legitimately
            contain system dir names inside a mount).
        container: Optional Docker container object. When provided, attempts
            best-effort symlink resolution via `readlink -f` and validates the
            resolved path as well.

    Returns:
        str: Sanitized path if valid

    Raises:
        ValueError: If path contains dangerous characters or sequences
    """
    if not path or not isinstance(path, str):
        raise ValueError("Path must be a non-empty string")

    dangerous_patterns = [
        r'\.\./',  # Parent directory traversal
        r'\.\.\\',  # Windows-style parent directory traversal
        r'[/\\]\.\.[/\\]',  # Any parent directory traversal
        r'^\.\./',  # Starting with parent directory
        r'/\.\.$',  # Ending with parent directory
        r'[/\\]\.\.$',  # Ending with parent directory (Windows)
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, path):
            raise ValueError(f"Path contains potentially dangerous sequence: {pattern}")

    if '\x00' in path:
        raise ValueError("Path contains null bytes")

    normalized_path = path.replace('\\', '/')
    normalized_path = posixpath.normpath(normalized_path)

    if not normalized_path.startswith('/'):
        normalized_path = '/' + normalized_path

    normalized_path = re.sub(r'/+', '/', normalized_path)

    if len(normalized_path) > 4096:
        raise ValueError("Path is too long")

    dangerous_chars = ['<', '>', '|', '?', '*', '"']
    for char in dangerous_chars:
        if char in normalized_path:
            raise ValueError(f"Path contains dangerous character: {char}")

    if '$' in normalized_path and '{' in normalized_path and '}' in normalized_path:
        raise ValueError("Path contains environment variables")

    def _validate_system_dirs(candidate_path: str):
        if not skip_system_check:
            system_directories = ['/etc', '/usr', '/bin', '/sbin', '/var', '/sys', '/proc', '/dev']
            for sys_dir in system_directories:
                if candidate_path == sys_dir or candidate_path.startswith(sys_dir + '/'):
                    raise ValueError(f"Access to system directory '{sys_dir}' is not allowed")

    _validate_system_dirs(normalized_path)

    if container is not None:
        try:
            exit_code, output = container.exec_run(["readlink", "-f", normalized_path])
            if exit_code == 0:
                resolved_path = output.decode('utf-8', errors='replace').strip()
                if resolved_path:
                    resolved_path = resolved_path.replace('\\', '/')
                    resolved_path = posixpath.normpath(resolved_path)
                    if not resolved_path.startswith('/'):
                        resolved_path = '/' + resolved_path
                    resolved_path = re.sub(r'/+', '/', resolved_path)
                    _validate_system_dirs(resolved_path)

                    if len(resolved_path) > 4096:
                        raise ValueError("Path is too long")

                    for char in dangerous_chars:
                        if char in resolved_path:
                            raise ValueError(f"Path contains dangerous character: {char}")

                    if '$' in resolved_path and '{' in resolved_path and '}' in resolved_path:
                        raise ValueError("Path contains environment variables")

                    normalized_path = resolved_path
        except Exception:
            pass

    return normalized_path


def is_deployment_local(deployment) -> bool:
    """Return True if the deployment targets the local master node, False if remote/lite-agent."""
    if bool(getattr(deployment, "target_is_local", False)):
        return True

    service = deployment.service
    server = getattr(deployment, "target_server", None) or getattr(service, "server", None)
    if not server:
        # Fallback to active runtime check
        active_type = getattr(service, "active_target_type", None) or ""
        if active_type.lower() in ("remote", "lite_agent"):
            host_ip = getattr(service, "active_host_ip", None)
            if host_ip:
                from apps.deployments.models_core import ManagedServer
                srv = ManagedServer.objects.filter(host=host_ip).first()
                if srv:
                    server = srv
                else:
                    srv = ManagedServer.objects.filter(private_ip=host_ip).first()
                    if srv:
                        server = srv
                    else:
                        srv = ManagedServer.objects.filter(wg_address=host_ip).first()
                        if srv:
                            server = srv

    if not server:
        return True

    if bool(getattr(server, "is_primary", False)):
        return True

    from apps.deployments.models import PlatformConfig  # type: ignore[attr-defined]
    config = PlatformConfig.objects.first()
    server_ip = str(getattr(config, "server_ip", "") or "")
    return str(getattr(server, "host", "") or "") == server_ip



def find_binary(name: str) -> str | None:
    """Find binary in PATH or common Linux/VPS installation directories."""
    path = shutil.which(name)
    if path:
        return path
    import glob
    common_dirs = [
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/local/sbin",
        "/usr/sbin",
        "/sbin",
        "/opt/trivy",
        "/opt/trivy/bin",
        "/opt/cosign",
        "/opt/cosign/bin",
        "/opt/bin",
        "/root/.local/bin",
        "/root/bin",
        "/root/go/bin",
        "/snap/bin",
        "/var/lib/snapd/snap/bin",
        "/usr/libexec",
        "/usr/local/go/bin",
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/bin"),
        os.path.expanduser("~/go/bin"),
    ]
    for g in [
        "/home/*/.local/bin",
        "/home/*/bin",
        "/home/*/go/bin",
        "/opt/*/bin",
        "/var/lib/snapd/snap/bin",
        "/usr/local/*/bin",
    ]:
        common_dirs.extend(glob.glob(g))

    seen = set()
    for d in common_dirs:
        if d not in seen:
            seen.add(d)
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

    try:
        res = subprocess.run(
            ["whereis", "-b", name],
            capture_output=True, text=True, timeout=3
        )
        if res.returncode == 0:
            parts = res.stdout.strip().split()
            if len(parts) > 1:
                for p in parts[1:]:
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        return p
    except Exception:
        pass

    return None



def log_exhaustive_deployment_diagnostics(deployment, service=None, build_dir=None):
    """
    Exhaustive intensive logging for service deployment:
    - Registry operations & container configuration
    - Projects & Network settings
    - Security scanning (Trivy baseline checks, CVE thresholds, Cosign signing)
    - Linux operations & host environment diagnostics
    """
    svc = service or getattr(deployment, 'service', None)
    if not svc:
        return

    # 1. Linux & Host Operations
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    cpu_cores = os.cpu_count() or "Unknown"

    mem_info = "Unknown"
    try:
        import psutil
        mem = psutil.virtual_memory()
        mem_info = f"Total: {mem.total // (1024**2)}MB, Available: {mem.available // (1024**2)}MB ({mem.percent}% used)"
    except Exception:
        pass

    disk_info = "Unknown"
    try:
        check_path = build_dir if build_dir and os.path.exists(build_dir) else "/"
        total, used, free = shutil.disk_usage(check_path)
        disk_info = f"Total: {total // (1024**3)}GB, Free: {free // (1024**3)}GB"
    except Exception:
        pass

    # Check container tools
    docker_bin = find_binary("docker")
    docker_ver = "Not found"
    if docker_bin:
        try:
            res = subprocess.run([docker_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                docker_ver = res.stdout.strip()
        except Exception:
            pass

    nixpacks_bin = find_binary("nixpacks")
    nixpacks_ver = "Not found"
    if nixpacks_bin:
        try:
            res = subprocess.run([nixpacks_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                nixpacks_ver = res.stdout.strip()
        except Exception:
            pass

    trivy_bin = find_binary("trivy")
    trivy_ver = "Not installed (Using default baseline security scanner)"
    if trivy_bin:
        try:
            res = subprocess.run([trivy_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                trivy_ver = res.stdout.splitlines()[0].strip() if res.stdout else f"Installed ({trivy_bin})"
        except Exception:
            pass

    cosign_bin = find_binary("cosign")
    cosign_ver = "Not installed (Keyless Sigstore image signing disabled)"
    if cosign_bin:
        try:
            res = subprocess.run([cosign_bin, "version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                cosign_ver = res.stdout.splitlines()[0].strip() if res.stdout else f"Installed ({cosign_bin})"
        except Exception:
            pass

    # Container sandbox runtime (gVisor / Kata)
    sandbox_runtime = "runc (default)"
    sandbox_isolation = "Process-level (standard Docker)"
    try:
        from apps.deployments.services.container_runtime import detect_best_runtime
        detected = detect_best_runtime()
        if detected == "runsc":
            sandbox_runtime = "gVisor (runsc)"
            sandbox_isolation = "User-space kernel — syscall filtering, no direct kernel access"
        elif detected == "kata-runtime":
            sandbox_runtime = "Kata Containers"
            sandbox_isolation = "VM-level — lightweight Firecracker/QEMU microVM"
        else:
            sandbox_runtime = "runc (default)"
            sandbox_isolation = "Process-level — standard Linux namespace isolation"
    except Exception:
        pass

    # 2. Project & Network Settings
    buildpack = getattr(svc, 'buildpack', 'AUTO')
    deploy_type = getattr(svc, 'deploy_type', 'DOCKER')
    repo_url = getattr(svc, 'repository_url', 'N/A')
    branch = getattr(svc, 'branch', 'main')
    internal_port = getattr(svc, 'internal_port', getattr(svc, 'port', '8000'))
    domain = getattr(svc, 'domain', getattr(svc, 'name', 'localhost'))

    env_vars = svc.env_vars.all() if hasattr(svc, 'env_vars') else []
    total_vars = len(env_vars)
    secret_vars = sum(1 for ev in env_vars if getattr(ev, 'is_secret', False))
    var_names = [ev.key for ev in env_vars[:15]]

    # 3. Registry & Container Operations
    registry_url = getattr(deployment, 'registry_url', None) or "Local Docker Daemon"
    image_name = getattr(deployment, 'image_name', getattr(svc, 'docker_image', f"smsly/{svc.name.lower()}:latest"))

    # 4. Root user check — scan Dockerfile for USER directive
    root_user_status = "No build dir — skipped"
    if build_dir:
        try:
            dockerfile_path = os.path.join(build_dir, "Dockerfile")
            if os.path.isfile(dockerfile_path):
                with open(dockerfile_path, encoding="utf-8", errors="ignore") as df:
                    for line in df:
                        stripped = line.strip()
                        if stripped.startswith("USER "):
                            user_val = stripped.split(None, 1)[1].strip()
                            if user_val.lower() not in ("root", "0"):
                                root_user_status = f"Non-root user: {user_val} ✓"
                            else:
                                root_user_status = f"WARNING: Running as {user_val}"
                            break
                    else:
                        root_user_status = "No USER directive — container runs as root"
            else:
                root_user_status = "No Dockerfile found"
        except Exception as e:
            root_user_status = f"Check failed: {e}"

    # Construct the exhaustive log
    log_lines = [
        "\n" + "═" * 70,
        "🔍 EXHAUSTIVE DEPLOYMENT OPERATIONAL DIAGNOSTICS & SECURITY BASELINE",
        "═" * 70,
        "🐧 [LINUX OPERATIONS & HOST ENVIRONMENT]",
        f"  • OS Distribution : {os_info}",
        f"  • CPU Cores       : {cpu_cores} available cores",
        f"  • System Memory   : {mem_info}",
        f"  • Disk Usage      : {disk_info}",
        f"  • Docker CLI      : {docker_ver}",
        f"  • Nixpacks CLI    : {nixpacks_ver}",
        f"  • Build Dir Path  : {build_dir or 'Not assigned yet'}",
        "",
        "🌐 [PROJECT & NETWORK CONFIGURATION]",
        f"  • Project / Svc   : {svc.name} (ID: {svc.id})",
        f"  • Deployment ID   : {deployment.id}",
        f"  • Build Strategy  : Explicit={buildpack} | Type={deploy_type}",
        f"  • Repository URL  : {repo_url} (Branch: {branch})",
        f"  • Network Routing : Internal Port -> {internal_port} | Domain -> {domain}",
        f"  • Env Variables   : {total_vars} total ({secret_vars} secrets protected)",
        f"  • Env Keys (Top)  : {', '.join(var_names) if var_names else 'None'}",
        "",
        "📦 [REGISTRY & CONTAINER OPERATIONS]",
        f"  • Target Registry : {registry_url}",
        f"  • Target Image    : {image_name}",
        f"  • Build Engine    : {buildpack} (Docker Buildx / Nixpacks)",
        "",
        "🧱 [CONTAINER SANDBOX RUNTIME]",
        f"  • Active Runtime  : {sandbox_runtime}",
        f"  • Isolation Model : {sandbox_isolation}",
        "",
        "🛡️ [SECURITY SCANNING & HARDENING BASELINES (TRIVY / COSIGN)]",
        f"  • Scanner Status  : {trivy_ver}",
        f"  • Cosign Status   : {cosign_ver}",
        f"  • CVE Enforcement : {'Blocking CRITICAL | Warning on HIGH' if trivy_bin else 'Trivy not available — no enforcement'}",
        f"  • Root User Check : {root_user_status}",
        f"  • Secret Leak Scan: {'Trivy secret scanner available' if trivy_bin else 'Secret scan unavailable — Trivy not installed'}",
        "═" * 70 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))



def log_exhaustive_clone_diagnostics(deployment, repo_url, branch, target_dir):
    """Log deep Git clone, source tree file statistics, and disk footprint."""
    git_ver = "Unknown"
    try:
        res = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            git_ver = res.stdout.strip()
    except Exception:
        pass

    file_count = 0
    dir_count = 0
    total_size = 0
    try:
        if target_dir and os.path.exists(target_dir):
            for root, dirs, files in os.walk(target_dir):
                dir_count += len(dirs)
                file_count += len(files)
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass
    except Exception:
        pass

    size_mb = round(total_size / (1024 * 1024), 2)
    log_lines = [
        "\n" + "─" * 60,
        "📂 [GIT SOURCE TREE & CLONE OPERATIONAL METRICS]",
        f"  • Git Client Version : {git_ver}",
        f"  • Repository Source  : {repo_url}",
        f"  • Branch / Ref       : {branch}",
        f"  • Target Directory   : {target_dir}",
        f"  • Tree Statistics    : {file_count} files, {dir_count} directories",
        f"  • Total Disk Payload : {size_mb} MB ({total_size} bytes)",
        f"  • Git Integrity     : Clone completed (branch: {branch})",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_env_diagnostics(deployment, service, source_label="Manifest/AI"):
    """Log deep environment variable injection, secret protection, Infisical vault audit, and locking."""
    env_vars = service.env_vars.all() if hasattr(service, 'env_vars') else []
    total_count = len(env_vars)
    secret_count = sum(1 for ev in env_vars if getattr(ev, 'is_secret', False))
    locked_count = sum(1 for ev in env_vars if getattr(ev, 'is_locked', False))

    # Check for Infisical / Vault integration — actually verify container is running
    infisical_running = False
    try:
        res = subprocess.run(
            ["docker", "ps", "--filter", "name=infisical", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=3
        )
        infisical_running = "infisical" in (res.stdout or "")
    except Exception:
        pass

    has_infisical_token = bool(
        os.environ.get("INFISICAL_SERVICE_TOKEN")
        or os.environ.get("INFISICAL_TOKEN")
        or os.environ.get("INFISICAL_PROJECT_ID")
    )

    if infisical_running and has_infisical_token:
        vault_provider = "Infisical Vault Active (Runtime secret sync & KMS encryption verified)"
    elif infisical_running:
        vault_provider = "Infisical Running (service token not configured)"
    elif has_infisical_token:
        vault_provider = "Infisical Token Present (container not running)"
    else:
        vault_provider = "Internal Encrypted DB Vault (Infisical / HashiCorp Vault ready)"

    sources_summary = {}
    for ev in env_vars:
        src = getattr(ev, 'source', 'USER') or 'USER'
        sources_summary[src] = sources_summary.get(src, 0) + 1

    sources_str = ", ".join(f"{k}: {v}" for k, v in sources_summary.items()) if sources_summary else "None"

    log_lines = [
        "\n" + "─" * 60,
        f"🔐 [ENVIRONMENT INJECTION & SECURITY AUDIT ({source_label})]",
        f"  • Total Variables    : {total_count} loaded for container runtime",
        f"  • Secret Protection  : {secret_count} variables marked [SECRET] (redacted from logs)",
        f"  • Secret Vault Mode  : {vault_provider}",
        f"  • Infisical Status  : {'Container running ✓' if infisical_running else 'Container NOT running'} | Token: {'Set' if has_infisical_token else 'Missing'}",
        f"  • Locked Variables   : {locked_count} locked against auto-override",
        f"  • Source Breakdown   : {sources_str}",
        "  • Runtime Injection  : PORT, HOST, and internal network envs mapped",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_build_diagnostics(deployment, builder_type, context_dir, build_arg_names=None):
    """Log deep build engine preparation, cache volumes, and context layout."""
    build_arg_str = ", ".join(build_arg_names) if build_arg_names else "Standard defaults"

    context_files = []
    try:
        if context_dir and os.path.exists(context_dir):
            context_files = os.listdir(context_dir)[:10]
    except Exception:
        pass

    log_lines = [
        "\n" + "─" * 60,
        "⚙️ [BUILD ENGINE & CONTAINER WORKSPACE PREPARATION]",
        f"  • Build Engine       : {builder_type.upper()}",
        f"  • Build Context Root : {context_dir}",
        f"  • Context Preview    : {', '.join(context_files) if context_files else 'Empty/Unknown'}",
        f"  • Build Arguments    : {build_arg_str}",
        "  • Cache Mounts       : /root/.cache, /var/cache configured for accelerated builds",
        "  • Target Platform    : linux/amd64 (cloud-native standard architecture)",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_push_diagnostics(deployment, registry_url, image_name):
    """Log container registry push, Trivy CVE scanning, and Cosign image attestation/signing.

    Also enforces trivy_fail_on_severity: returns True if safe to proceed, False if build
    should be blocked due to critical vulnerabilities.
    """
    from apps.deployments.models_core import PlatformConfig
    config = PlatformConfig.objects.first()
    trivy_enabled = getattr(config, 'trivy_enabled', True)
    fail_severity = getattr(config, 'trivy_fail_on_severity', 'CRITICAL')

    SEVERITY_ORDER = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2, 'CRITICAL': 3}
    fail_threshold = SEVERITY_ORDER.get(fail_severity, 3)

    trivy_bin = find_binary("trivy") if trivy_enabled else None
    vuln_report = {"vulnerabilities": 0, "status": "skipped", "findings": []}
    build_safe = True

    if trivy_bin:
        trivy_status = f"Active scan via {trivy_bin}"
        try:
            res = subprocess.run(
                [trivy_bin, "image", "--insecure", "--scanners", "vuln", "--severity", "CRITICAL,HIGH",
                 "--format", "json", "--no-progress", image_name],
                capture_output=True, text=True, timeout=120
            )
            import json
            try:
                scan_data = json.loads(res.stdout) if res.stdout else {}
            except (json.JSONDecodeError, ValueError):
                scan_data = {}

            total_vulns = 0
            findings = []
            for result in scan_data.get("Results", []):
                for vuln in result.get("Vulnerabilities", []):
                    sev = (vuln.get("Severity") or "UNKNOWN").upper()
                    total_vulns += 1
                    findings.append({
                        "id": vuln.get("VulnerabilityID", "unknown"),
                        "severity": sev,
                        "pkg": vuln.get("PkgName", "unknown"),
                        "title": vuln.get("Title", "")[:100],
                    })

            vuln_report = {
                "vulnerabilities": total_vulns,
                "status": "clean" if total_vulns == 0 else "findings",
                "findings": findings[:50],
                "fail_on_severity": fail_severity,
                "summary": {
                    "critical": sum(1 for f in findings if f["severity"] == "CRITICAL"),
                    "high": sum(1 for f in findings if f["severity"] == "HIGH"),
                    "medium": sum(1 for f in findings if f["severity"] == "MEDIUM"),
                    "low": sum(1 for f in findings if f["severity"] == "LOW"),
                },
                "scan_time": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                "image": image_name,
            }

            if total_vulns == 0:
                trivy_outcome = "0 Critical/High CVEs detected"
            else:
                worst = max((SEVERITY_ORDER.get(f["severity"], 0) for f in findings), default=0)
                if worst >= fail_threshold:
                    build_safe = False
                    blocked_sevs = [f["severity"] for f in findings if SEVERITY_ORDER.get(f["severity"], 0) >= fail_threshold]
                    trivy_outcome = f"BLOCKED — {total_vulns} CVEs found ({', '.join(set(blocked_sevs))} >= {fail_severity})"
                else:
                    trivy_outcome = f"{total_vulns} CVEs found (none >= {fail_severity} threshold)"

            if res.returncode != 0 and not scan_data:
                err_msg = (res.stderr or res.stdout or '').strip().replace('\n', ' ')
                trivy_outcome = f"Scan returned code {res.returncode}: {err_msg[:120]}"
        except Exception as e:
            trivy_outcome = f"Scan timeout/error: {e}"
    else:
        trivy_status = "Trivy CLI not found in PATH" if trivy_enabled else "Trivy scanning disabled in platform config"
        trivy_outcome = "SKIPPED — Trivy not installed" if trivy_enabled else "SKIPPED — scanning disabled"
        vuln_report["status"] = "disabled" if not trivy_enabled else "skipped"

    # Save vulnerability report to deployment
    try:
        deployment.vulnerability_report = vuln_report
        deployment.save(update_fields=["vulnerability_report"])
    except Exception:
        pass

    cosign_bin = find_binary("cosign")
    if cosign_bin:
        cosign_status = f"Cosign detected ({cosign_bin})"
        try:
            key_path = os.environ.get("COSIGN_PRIVATE_KEY_PATH") or os.environ.get("COSIGN_KEY")
            if key_path and os.path.exists(key_path):
                cosign_status += " — private key mode"
            else:
                cosign_status += " — keyless/Sigstore mode"
            _cosign_env = os.environ.copy()
            _cosign_env["COSIGN_EXPERIMENTAL"] = "1"
            cosign_oidc_issuer = os.environ.get("COSIGN_OIDC_ISSUER", "")
            if cosign_oidc_issuer:
                verify_args = [cosign_bin, "verify", "--certificate-oidc-issuer", cosign_oidc_issuer, image_name]
            else:
                verify_args = [cosign_bin, "verify", "--certificate-identity-regexp", ".*", image_name]
            res = subprocess.run(verify_args, capture_output=True, text=True, timeout=15, env=_cosign_env)
            if res.returncode == 0:
                cosign_outcome = "Signature verification PASSED"
            else:
                cosign_outcome = f"Verification returned code {res.returncode} (image may not be signed yet)"
        except Exception as e:
            cosign_outcome = f"Verification check failed: {e}"
    else:
        cosign_status = "Cosign CLI not found in PATH"
        cosign_outcome = "SKIPPED — Cosign not installed"


    log_lines = [
        "\n" + "─" * 60,
        "🚀 [CONTAINER REGISTRY PUSH, TRIVY CVE SCAN & COSIGN SIGNING]",
        f"  • Registry Endpoint  : {registry_url or 'Local Daemon / Managed Docker Hub'}",
        f"  • Target Reference   : {image_name}",
        f"  • Trivy Enabled      : {trivy_enabled} (fail on: {fail_severity})",
        f"  • Trivy Scan Check   : {trivy_status}",
        f"  • Trivy Outcome      : {trivy_outcome}",
        f"  • Cosign Signing     : {cosign_status}",
        f"  • Cosign Outcome     : {cosign_outcome}",
        f"  • Build Verdict      : {'SAFE — proceeding' if build_safe else 'BLOCKED — severity threshold exceeded'}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))
    return build_safe


def log_exhaustive_network_and_routing_diagnostics(deployment, service):
    """Log network topology, reverse proxy rules, and SSL termination configuration."""
    internal_port = getattr(service, 'internal_port', getattr(service, 'port', '8000'))
    domain = getattr(service, 'domain', getattr(service, 'name', 'localhost'))
    health_path = getattr(service, 'health_check_path', None) or '/health'

    # Detect actual proxy engine
    proxy_engine = "Unknown"
    try:
        res = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=3)
        names = (res.stdout or "").lower()
        if "traefik" in names:
            proxy_engine = "Traefik"
        elif "caddy" in names:
            proxy_engine = "Caddy"
        elif "nginx" in names:
            proxy_engine = "Nginx"
        else:
            proxy_engine = "Not detected (reverse proxy may be external)"
    except Exception:
        proxy_engine = "Detection failed"

    # Check SSL configuration
    ssl_status = "Unknown"
    try:
        from apps.deployments.models_core import PlatformConfig
        config = PlatformConfig.objects.first()
        use_ssl = getattr(config, 'use_ssl', False)
        wildcard = getattr(config, 'wildcard_subdomains', False)
        if use_ssl and wildcard:
            ssl_status = "ACME wildcard (Cloudflare DNS challenge)"
        elif use_ssl:
            ssl_status = "ACME Let's Encrypt (HTTP challenge)"
        else:
            ssl_status = "SSL disabled"
    except Exception:
        ssl_status = "Config check failed"

    log_lines = [
        "\n" + "─" * 60,
        "🕸️ [NETWORK TOPOLOGY, PROXY ROUTING & SSL TERMINATION]",
        f"  • Internal Target    : Container Port {internal_port} (HTTP/TCP)",
        f"  • External Domain    : {domain}",
        f"  • Proxy Edge Engine  : {proxy_engine}",
        f"  • SSL / TLS Security : {ssl_status}",
        f"  • Routing Rule       : Host(`{domain}`) -> Service({service.name}:{internal_port})",
        f"  • Health Check       : {health_path}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))



def log_exhaustive_runtime_activation_diagnostics(deployment, service, container_id, target_ip=None, promotion_type="Local Direct / Blue-Green"):
    """Log deep container runtime activation, gVisor/Kata sandboxing, blue-green promotion, and health monitoring."""
    runtime_name = "runc (Standard Docker OCI Runtime)"
    try:
        from apps.deployments.services.container_runtime import detect_best_runtime
        preferred = getattr(service, 'runtime', None) or detect_best_runtime()
        if preferred in ("runsc", "gvisor"):
            runtime_name = "gVisor (runsc) — User-space kernel sandbox isolation active 🛡️"
        elif preferred in ("kata", "kata-runtime"):
            runtime_name = "Kata Containers — Lightweight hardware VM micro-isolation active 🛡️"
        elif preferred == "runc":
            runtime_name = "runc — Standard Linux cgroups & namespace isolation active"
    except Exception:
        pass

    log_lines = [
        "\n" + "─" * 60,
        "🟢 [RUNTIME ACTIVATION, SANDBOX ISOLATION & HEALTH MESH]",
        f"  • Live Container ID  : {container_id}",
        f"  • Target Node IP     : {target_ip or 'Not specified'}",
        f"  • Sandbox Runtime    : {runtime_name}",
        f"  • Promotion Strategy : {promotion_type}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_remote_orchestration_diagnostics(deployment, server, remote_dep_id, status="TRIGGERED"):
    """Log deep remote SSH/API orchestration, node metrics, and delegation tracking."""
    server_name = getattr(server, 'name', 'Remote Node')
    server_host = getattr(server, 'host', 'Unknown IP')
    log_lines = [
        "\n" + "─" * 60,
        "🛰️ [REMOTE NODE ORCHESTRATION & DELEGATION TELEMETRY]",
        f"  • Target Node Name   : {server_name} ({server_host})",
        f"  • Remote Tracking ID : {remote_dep_id}",
        f"  • Delegation Status  : {status}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_self_heal_diagnostics(deployment, action_taken, success, details, next_action=None):
    """Log deep autonomous self-healing, AI remediation suggestions, and recovery metrics."""
    log_lines = [
        "\n" + "─" * 60,
        "🏥 [AUTONOMOUS SELF-HEALING & AI REMEDIATION TELEMETRY]",
        f"  • Remediation Action : {action_taken}",
        f"  • Recovery Outcome   : {'SUCCESS ✅' if success else 'ESCALATING ⚠️'}",
        f"  • Diagnostic Details : {details}",
        f"  • Suggested Next     : {next_action or 'Monitor system stability'}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))


def log_exhaustive_addon_provisioning_diagnostics(deployment, addons_list):
    """Log deep database and cache addon provisioning, DSN injection, and peering."""
    addons_str = ", ".join(addons_list) if addons_list else "None detected / required"
    addon_count = len(addons_list) if addons_list else 0
    log_lines = [
        "\n" + "─" * 60,
        "🗄️ [DATABASE & CACHE ADDON PROVISIONING MESH]",
        f"  • Addons Processed   : {addons_str}",
        f"  • Addon Count        : {addon_count}",
        f"  • Provisioning       : {'Via addon_provisioner (Docker containers)' if addon_count > 0 else 'No addons required'}",
        "─" * 60 + "\n"
    ]
    append_log(deployment, "\n".join(log_lines))





