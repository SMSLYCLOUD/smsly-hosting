# ============================================================================
# REFACTOR IN PROGRESS — see docs/REFACTOR_PLAN_VIEWS_TASKS.md
# This file is being split into per-domain siblings. New code should be
# added to the appropriate sibling file (e.g. views_servers.py, tasks_health.py).
# ============================================================================
# pylint: disable=invalid-name
# pylint: disable=too-many-lines
"""Views module."""
import contextlib
import hmac
import logging
import os
import posixpath
import re
import threading
import uuid

from celery.result import AsyncResult
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, models, transaction
from django.db.models import (
    Avg,
    DurationField,
    ExpressionWrapper,
    F,
    Q,
)
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from rest_framework import (
    authentication,
    filters,
    parsers,
    permissions,
    serializers,
    status,
    viewsets,
)
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.cloud.docker_client import get_docker_client
from apps.cloud.models import CloudProvider
from apps.deployments.utils import resolve_running_container
from apps.deployments.utils_file_browser import exec_file_list
from apps.teams.permissions import (
    assert_can_delete,
    assert_can_write,
    get_team_q_filter,
)

from .ai_router import (
    DEFAULT_AI_ROUTER_API_BASE,
    DEFAULT_AI_ROUTER_UI_BASE,
    DEFAULT_BRAID_ALIAS,
    is_ai_router_service,
    persist_ai_router_config,
    serialize_ai_router_config,
)
from .domain_utils import normalize_domain
from .models import (  # type: ignore[attr-defined]    # models.py hub no longer re-exports; classes live in models_core.py.
    Deployment,
    EnvironmentVariable,
    PlatformConfig,
    Service,
)
from .models_audit import AuditLog
from .models_backup import BackupSchedule, ServerBackup, ServiceBackup, ServiceSnapshot, SnapshotSchedule
from .rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from .serializers import (
    BackupScheduleSerializer,
    DeploymentApproveSerializer,
    DeploymentSerializer,
    DeploymentTimelineSerializer,
    DeploymentTriggerSerializer,
    EnvVarSerializer,
    InstantRollbackSerializer,
    ServerBackupSerializer,
    ServiceBackupSerializer,
    ServiceSerializer,
    ServiceSnapshotDiffSerializer,
    ServiceSnapshotRestoreSerializer,
    ServiceSnapshotSerializer,
    SnapshotScheduleSerializer,
)
from .services.server_guard import ServerGuard
from .tasks import (
    create_server_backup_task,
    create_service_backup_task,
    enqueue_smart_deploy_task,
    restore_service_backup_task,
    resume_deploy_task,
    smart_deploy_task,
)
from .utils import validate_and_sanitize_path


def _resolve_encryption_key(request):
    """Extract encryption key from request data (encryption_key, key_id, or uploaded JSON)."""
    key = request.data.get('encryption_key', '').strip()
    if key:
        return key
    key_id = request.data.get('key_id', '').strip()
    if key_id:
        from apps.deployments.services.backup_service import BackupService
        key_material = BackupService.lookup_key_by_id(key_id)
        if key_material:
            return key_material
    key_file = request.FILES.get('key_file')
    if key_file:
        import json
        try:
            payload = json.loads(key_file.read())
            if isinstance(payload, dict):
                return payload.get('encryption_key', '').strip() or None
            return str(payload).strip() or None
        except (json.JSONDecodeError, AttributeError, OSError):
            pass
    return None


class ZeroTrustHMACAuthentication(authentication.BaseAuthentication):
    """
    Authenticate requests from peer nodes using HMAC V2.
    Required headers: X-Gateway-Signature-V2, X-Request-Timestamp,
    X-Request-Nonce.

    SECURITY (Batch G): the nonce is now mandatory and bound into the
    signed payload. Without this, a captured request can be replayed
    for the full timestamp window. Callers must generate a
    cryptographically-random nonce per request, send it in
    ``X-Request-Nonce``, and include it in the signed payload as
    ``{method}|{path}|{timestamp}|{nonce}|{body_hash}``.
    """
    def authenticate(self, request):
        import hashlib
        import hmac
        import time

        from django.contrib.auth import get_user_model
        User = get_user_model()

        signature = request.headers.get("X-Gateway-Signature-V2", "")
        timestamp = request.headers.get("X-Request-Timestamp", "")
        nonce = request.headers.get("X-Request-Nonce", "")
        if not signature or not timestamp or not nonce:
            return None

        # Verify timestamp freshness (1 min window)
        try:
            req_ts = int(timestamp)
            if abs(int(time.time()) - req_ts) > 60:
                raise authentication.AuthenticationFailed("Timestamp expired")
        except ValueError:
            raise authentication.AuthenticationFailed("Invalid timestamp")

        # SECURITY: nonce replay protection. Each nonce is one-use
        # within the freshness window.
        from django.core.cache import cache
        nonce_key = f"hmac_nonce:{nonce}"
        if cache.get(nonce_key):
            raise authentication.AuthenticationFailed("Nonce already used")
        cache.set(nonce_key, "1", timeout=120)

        # Verify HMAC
        gw_secret = getattr(settings, "GATEWAY_SECRET", settings.SECRET_KEY)
        method = request.method
        path = request.get_full_path()

        try:
            body = request.body
        except Exception:
            body = b""

        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"{method}|{path}|{timestamp}|{nonce}|{body_hash}"
        expected = hmac.new(gw_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise authentication.AuthenticationFailed("Invalid HMAC signature")

        # Authentication success — use the first active superuser as the actor
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            raise authentication.AuthenticationFailed("No admin user available")

        return (admin, None)

logger = logging.getLogger(__name__)

# SECURITY (Issue 21): the SMSLY_DISABLE_TIER_GATES env var, when set,
# silently unlocks all paid tier features. An operator who flips the
# env var should leave a fingerprint in the immutable AuditLog.
# ``_check_tier_gates_disabled()`` returns the current boolean state
# of the flag and records an AuditLog entry on the first consult per
# process. Call this helper instead of reading the env var or the
# settings attribute directly. The flag is also documented in
# config/settings.py.
_TIER_GATES_LOGGED = False


def _check_tier_gates_disabled() -> bool:
    """Return True if the SMSLY_DISABLE_TIER_GATES bypass is active.

    On the first consult in a given process where the flag is on,
    record an immutable AuditLog entry so the bypass is never silent.
    Checks PlatformConfig DB first, then env var fallback.
    """
    global _TIER_GATES_LOGGED
    try:
        from .models_core import PlatformConfig
        db_val = PlatformConfig.load().smsly_disable_tier_gates
    except Exception:
        db_val = None
    if db_val is not None and db_val:
        raw = str(db_val)
    else:
        raw = str(
            getattr(settings, "SMSLY_DISABLE_TIER_GATES", False)
            or os.environ.get("SMSLY_DISABLE_TIER_GATES", "")
        ).strip().lower()
    enabled = raw in ("1", "true", "yes", "on")
    if enabled and not _TIER_GATES_LOGGED:
        try:
            AuditLog.objects.create(
                actor="system",
                action="TIER_GATES_DISABLED",
                target="global",
                metadata={
                    "env_var": "SMSLY_DISABLE_TIER_GATES",
                    "value": os.environ.get(
                        "SMSLY_DISABLE_TIER_GATES",
                        getattr(settings, "SMSLY_DISABLE_TIER_GATES", ""),
                    ),
                },
            )
        except Exception as exc:
            logger.error("Failed to audit-log SMSLY_DISABLE_TIER_GATES: %s", exc)
        _TIER_GATES_LOGGED = True
    return enabled

MAINTENANCE_ACTIONS = {
    "registry_gc": {
        "flag": "--gc",
        "label": "Garbage Collect Private Registry",
        "queued_message": "Registry GC queued. Unused layers will be deleted.",
        "lock_ttl": 3600,
    },
    "build_cache": {
        "flag": "--clear-build-cache",
        "label": "Clear BuildKit Caches",
        "queued_message": "Build cache cleanup queued.",
        "lock_ttl": 900,
    },
    "clear": {
        "flag": "--clear",
        "label": "Clear orphaned containers",
        "queued_message": "Cleanup queued. Stale containers and caches will be cleared in the background.",
        "lock_ttl": 900,
    },
    "refresh": {
        "flag": "--refresh",
        "label": "Sync proxy routing",
        "queued_message": "Proxy sync queued. Caddy will reload from the latest routing config shortly.",
        "lock_ttl": 600,
    },
    "update": {
        "flag": "--update",
        "label": "Update platform",
        "queued_message": "Platform update queued. The host updater will pull and rebuild shortly.",
        "lock_ttl": 1800,
    },
}


def _error_response(code: str, message: str, *, details=None, user_action="", retryable=False, http_status_code=400):
    trace_id = str(uuid.uuid4())
    logger.error("api_error code=%s trace_id=%s details=%s", code, trace_id, details or {})
    return Response(
        {
            "success": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "user_action": user_action,
                "retryable": retryable,
                "trace_id": trace_id,
            },
        },
        status=http_status_code,
    )


class CleanupFileResponse(FileResponse):
    """FileResponse that deletes the underlying file when closed."""
    def __init__(self, *args, **kwargs):
        self._file_path = kwargs.pop('file_path', None)
        block_size = kwargs.pop('block_size', None) or kwargs.pop('blksize', None)
        super().__init__(*args, **kwargs)
        self.block_size = block_size or _BACKUP_DOWNLOAD_BLOCK_SIZE

    def close(self):
        super().close()
        if self._file_path and os.path.exists(self._file_path):
            with contextlib.suppress(OSError):
                os.remove(self._file_path)
        if self._file_path:
            parent = os.path.dirname(os.path.abspath(self._file_path))
            if parent and os.path.basename(parent).startswith('smsly-decrypted-'):
                with contextlib.suppress(OSError):
                    os.rmdir(parent)


_BACKUP_DOWNLOAD_BLOCK_SIZE = 1024 * 1024
_BACKUP_DOWNLOAD_CONTENT_TYPE = "application/gzip"


def _backup_download_headers(response, file_size: int | None, filename: str):
    response['Content-Type'] = _BACKUP_DOWNLOAD_CONTENT_TYPE
    response['Accept-Ranges'] = 'bytes'
    response['X-Accel-Buffering'] = 'no'
    response['Cache-Control'] = 'private, no-store'
    response['Content-Disposition'] = content_disposition_header(True, filename)
    if file_size is not None:
        response['Content-Length'] = str(file_size)
    return response


def _verify_signed_download(signed_value: str, expected_pk: str, max_age: int = 300) -> bool:
    """Verify a signed download token. Returns True if valid and not expired."""
    try:
        payload = signing.TimestampSigner().unsign_object(signed_value, max_age=max_age)
        return str(payload.get('pk')) == str(expected_pk)
    except (signing.BadSignature, signing.SignatureExpired):
        return False


def _generate_signed_download_url(request, obj_pk: str, url_name: str, path_params: dict | None = None) -> str:
    """Generate a signed download URL valid for 5 minutes."""
    import time

    from django.urls import reverse
    payload = {'pk': str(obj_pk), 'ts': int(time.time())}
    signed = signing.TimestampSigner().sign_object(payload)
    from urllib.parse import urlencode
    params = {'signed': signed}
    if path_params:
        params.update(path_params)
    path = reverse(url_name, args=[obj_pk])
    return request.build_absolute_uri(f"{path}?{urlencode(params)}")


def _parse_single_range(range_header: str, file_size: int):
    if not range_header or not range_header.startswith('bytes='):
        return None
    raw_range = range_header.split('=', 1)[1].strip()
    if ',' in raw_range or '-' not in raw_range:
        raise ValueError("Only a single byte range is supported")
    start_raw, end_raw = raw_range.split('-', 1)
    if not start_raw:
        suffix_length = int(end_raw)
        if suffix_length <= 0:
            raise ValueError("Invalid suffix range")
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1
    if start < 0 or end < start or start >= file_size:
        raise ValueError("Requested range is not satisfiable")
    return start, min(end, file_size - 1)


def _file_iterator(file_path: str, start: int = 0, end: int | None = None, cleanup_path: str | None = None):
    try:
        with open(file_path, 'rb') as file_obj:
            file_obj.seek(start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                read_size = _BACKUP_DOWNLOAD_BLOCK_SIZE if remaining is None else min(_BACKUP_DOWNLOAD_BLOCK_SIZE, remaining)
                chunk = file_obj.read(read_size)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
    finally:
        if cleanup_path and os.path.exists(cleanup_path):
            with contextlib.suppress(OSError):
                os.remove(cleanup_path)
        if cleanup_path:
            parent = os.path.dirname(os.path.abspath(cleanup_path))
            if parent and os.path.basename(parent).startswith('smsly-decrypted-'):
                with contextlib.suppress(OSError):
                    os.rmdir(parent)


def _open_backup_download_response(request, file_path: str, filename: str, cleanup_path: str | None = None):
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get('Range') or request.META.get('HTTP_RANGE')
    from django.http import HttpResponseBase
    response: HttpResponseBase
    if range_header:
        try:
            start, end = _parse_single_range(range_header, file_size)
        except (TypeError, ValueError):
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{file_size}'
            response['Accept-Ranges'] = 'bytes'
            return response
        response = StreamingHttpResponse(
            _file_iterator(file_path, start, end, cleanup_path),
            status=206,
            content_type=_BACKUP_DOWNLOAD_CONTENT_TYPE,
        )
        response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        response['Content-Length'] = str(end - start + 1)
        return _backup_download_headers(response, None, filename)

    response = CleanupFileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=filename,
        file_path=cleanup_path,
        block_size=_BACKUP_DOWNLOAD_BLOCK_SIZE,
    )
    return _backup_download_headers(response, file_size, filename)


class EmptySerializer(serializers.Serializer):
    """Schema placeholder for APIViews without request/response bodies."""


class CaddySecretOrAdminPermission(permissions.BasePermission):
    """
    Permission gate for the Caddy ``on_demand_tls`` 'ask' endpoint.

    Allows access if EITHER:

    * the request carries ``secret`` query param matching
      ``CADDY_ASK_SECRET`` (embedded in the Caddyfile ask URL as
      ``?secret=<value>`` — Caddy v2 can send query params), OR
    * the request is from an authenticated admin user.

    ``CADDY_ASK_SECRET`` is read from PlatformConfig DB first, then
    falls back to the ``CADDY_ASK_SECRET`` env var. If neither is set,
    the endpoint still allows access for backward compatibility with
    existing Caddyfiles, protected by domain verification + rate limits.
    """

    message = "Caddy ask endpoint requires a valid secret or admin authentication."

    def has_permission(self, request, view):
        expected = self._get_expected_secret()
        if expected:
            provided = request.query_params.get("secret", "")
            if "?domain=" in provided:
                provided = provided.split("?domain=")[0]
            if provided and hmac.compare_digest(provided, expected):
                return True
            # Also check X-Caddy-Secret header for older Caddyfile compatibility
            header_provided = request.headers.get("X-Caddy-Secret", "")
            if header_provided and hmac.compare_digest(header_provided, expected):
                return True
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False) and (
            getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)
        ):
            return True
        # No secret configured — allow through with domain + rate limit protections only
        return bool(not expected)

    @staticmethod
    def _get_expected_secret():
        """Return CADDY_ASK_SECRET from PlatformConfig DB, with env var fallback."""
        try:
            from .models_core import PlatformConfig
            cfg = PlatformConfig.load()
            db_secret = str(getattr(cfg, 'caddy_ask_secret', '') or '').strip()
            if db_secret:
                return db_secret
        except Exception:
            pass
        return str(getattr(settings, "CADDY_ASK_SECRET", "") or "")


_IN_PROGRESS_DEPLOYMENT_STATUSES = [
    Deployment.Status.QUEUED,
    Deployment.Status.BUILDING,
    Deployment.Status.DEPLOYING,
    'REVIEW',  # Also block if awaiting review
]


def _cancel_stale_in_progress_deployments(service):
    """
    Cancel stale in-progress deployments superseded by a newer ACTIVE deploy,
    OR deployments that have been stuck in BUILDING for > 30 minutes.
    This prevents zombie QUEUED rows from permanently blocking new deploys.
    """
    from datetime import timedelta

    from django.utils import timezone

    stale_threshold = timezone.now() - timedelta(minutes=15)
    service.deployments.filter(
        status=Deployment.Status.BUILDING,
        updated_at__lt=stale_threshold
    ).update(
        status=Deployment.Status.FAILED,
        ai_diagnosis="Automatically cancelled: Deployment was stuck in BUILDING state for more than 15 minutes."
    )

    latest_active = (
        service.deployments
        .filter(status=Deployment.Status.ACTIVE)
        .order_by('-created_at')
        .first()
    )
    if not latest_active:
        return 0

    stale_qs = service.deployments.filter(
        status__in=_IN_PROGRESS_DEPLOYMENT_STATUSES,
        created_at__lte=latest_active.created_at,
    )
    count = stale_qs.count()
    if count:
        stale_qs.update(status=Deployment.Status.CANCELLED, finished_at=timezone.now())
    return count


def _setup_provider_webhook(user, repo_url: str):
    """Dispatch webhook setup to the correct provider based on repo URL."""
    from urllib.parse import urlparse as _urlparse
    hostname = _urlparse(repo_url).hostname or ''
    if 'github' in hostname:
        from apps.deployments.services.github_webhooks import setup_github_webhook
        setup_github_webhook(user, repo_url)
    elif 'gitlab' in hostname:
        from apps.deployments.services.gitlab_webhooks import setup_gitlab_webhook
        setup_gitlab_webhook(user, repo_url)
    elif 'bitbucket' in hostname:
        from apps.deployments.services.bitbucket_webhooks import setup_bitbucket_webhook
        setup_bitbucket_webhook(user, repo_url)


def _has_active_deployment(service):
    """
    Check if a service already has an active deployment in progress.
    Returns the existing deployment if found, None otherwise.
    Prevents rapid-fire deployment spam from the dashboard.
    Uses select_for_update to prevent race conditions on concurrent deploys.
    """
    from django.db import transaction
    _cancel_stale_in_progress_deployments(service)
    with transaction.atomic():
        qs = Deployment.objects.select_for_update().filter(
            service=service,
            status__in=_IN_PROGRESS_DEPLOYMENT_STATUSES,
        ).order_by('-created_at')[:1]
        return qs.first()


def _resolve_provider_for_service(service: Service, prefer_local: bool = False):
    """
    Strict one-to-one provider resolution. No silent fallbacks.
    - If service has a provider, it MUST be active and we return it.
    - If no provider but prefer_local, return LOCAL if active.
    - Fail explicitly if intended target unavailable.
    """
    if service.provider:
        if service.provider.is_active:
            return service.provider
        return None # Explicitly fail

    if prefer_local:
        local = CloudProvider.objects.filter(
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True
        ).first()
        if local:
            return local
        return None

    # Implicit default: if no explicit target, try to find one but don't fallback silently later.
    # We will pick a default global remote or local, but once picked, it's fixed.
    remote = CloudProvider.objects.filter(
        provider_type=CloudProvider.ProviderType.REMOTE,
        is_active=True
    ).first()
    if remote:
        return remote

    return CloudProvider.objects.filter(
        provider_type=CloudProvider.ProviderType.LOCAL,
        is_active=True
    ).first()

def _normalize_request_domain(raw_domain: str):
    """Normalize and validate user-provided domains."""
    try:
        return normalize_domain(raw_domain), None
    except ValueError as exc:
        return None, str(exc)


def _rewrite_public_domain(current_domain: str, old_base_domain: str, new_base_domain: str) -> str | None:
    """Rewrite a service public domain from one Grid platform base domain to another."""
    current = str(current_domain or "").strip().lower().rstrip(".")
    old_base = str(old_base_domain or "").strip().lower().rstrip(".")
    new_base = str(new_base_domain or "").strip().lower().rstrip(".")
    if not current or not old_base or not new_base or old_base == new_base:
        return None

    if current == old_base:
        return new_base

    suffix = f".{old_base}"
    if not current.endswith(suffix):
        return None

    prefix = current[:-len(suffix)].rstrip(".")
    if not prefix:
        return new_base
    return f"{prefix}.{new_base}"


def _service_for_domain(domain: str):
    """Find service routed by this public/custom domain."""
    direct = Service.objects.filter(public_domain=domain, public_domain_hidden=False).first()
    if direct:
        return direct
    try:
        return Service.objects.filter(custom_domains__contains=[domain]).first()
    except Exception:
        pass
    for service in Service.objects.only("id", "custom_domains")[:500]:
        values = [
            str(value or "").strip().lower()
            for value in (service.custom_domains or [])
            if str(value or "").strip()
        ]
        if domain in values:
            return service
    return None


def _parse_bool(value):
    """Safely parse booleans from JSON or form-encoded payloads."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


_DEPLOY_TARGET_MISSING = object()
_LOCAL_DEPLOY_TARGET_VALUES = {
    "",
    "local",
    "localhost",
    "controller",
    "master",
    "primary",
    "none",
    "null",
}


def _is_local_deploy_target(value) -> bool:
    """Return True for explicit client values that mean the local controller."""
    if value is None:
        return True
    return str(value).strip().lower() in _LOCAL_DEPLOY_TARGET_VALUES


def _ensure_local_server_record():
    """Auto-register the local controller as a ManagedServer if none exists.

    Called when no ManagedServer is found during service creation. Creates
    a primary ONLINE record so subsequent lookups succeed.
    """
    import socket
    from .models_core import ManagedServer

    # Determine the local IP (the one the host is reachable on)
    try:
        host = socket.gethostbyname(socket.gethostname())
    except Exception:
        host = "127.0.0.1"

    # Load platform config for the domain
    try:
        from apps.deployments.models_core import PlatformConfig
        config = PlatformConfig.load()
        domain = getattr(config, "domain", "") or ""
        api_url = f"http://{host}:8090"
    except Exception:
        domain = ""
        api_url = f"http://{host}:8090"

    # Use the first admin user as owner
    from django.contrib.auth import get_user_model
    User = get_user_model()
    admin = User.objects.filter(is_superuser=True).first()
    if not admin:
        logger.warning("Cannot auto-register local server: no admin user found")
        return None

    server = ManagedServer.objects.create(
        owner=admin,
        name="Master Node (Auto-registered)",
        host=host,
        api_url=api_url,
        is_primary=True,
        status=ManagedServer.Status.ONLINE,
        provision_status=ManagedServer.ProvisionStatus.DONE,
        allow_user_workloads=False,
    )
    logger.info("Auto-registered local server record: %s (%s)", server.name, host)
    return server


def _resolve_local_provider():
    return CloudProvider.objects.filter(
        provider_type=CloudProvider.ProviderType.LOCAL,
        is_active=True,
    ).first()


def _resolve_provider_for_target(service: Service, *, target_is_local: bool = False):
    """Resolve a provider for the chosen deployment target."""
    if target_is_local:
        return _resolve_local_provider()
    return _resolve_provider_for_service(service)


def _resolve_requested_deploy_target(request, service: Service):
    """
    Resolve the optional per-deploy target.

    Omitted target_server_id keeps legacy behavior: deploy where the service is
    assigned. Explicit null/empty/"local" means this one deployment runs on the
    local controller even if the service is normally assigned to a remote node.
    """
    raw_target = request.data.get('target_server_id', _DEPLOY_TARGET_MISSING)
    if raw_target is _DEPLOY_TARGET_MISSING:
        effective_server = getattr(service, 'server', None)
        # When no server is assigned to the service, fall back to local
        # deployment so the provider resolution picks LOCAL and Caddy
        # routing is set up correctly.
        return {
            "ok": True,
            "specified": False,
            "target_server": None,
            "target_is_local": effective_server is None,
            "effective_server": effective_server,
        }

    if _is_local_deploy_target(raw_target):
        return {
            "ok": True,
            "specified": True,
            "target_server": None,
            "target_is_local": True,
            "effective_server": None,
        }

    from apps.deployments.models_core import ManagedServer

    target_id = str(raw_target or "").strip()
    queryset = ManagedServer.objects.all()
    if not request.user.is_superuser:
        queryset = queryset.filter(owner=request.user)
    target_server = queryset.filter(id=target_id).first()
    if not target_server:
        return {
            "ok": False,
            "response": Response(
                {'error': f'Server {target_id} not found'},
                status=status.HTTP_400_BAD_REQUEST,
            ),
        }

    if target_server.is_primary:
        return {
            "ok": True,
            "specified": True,
            "target_server": None,
            "target_is_local": True,
            "effective_server": None,
        }

    if target_server.status != ManagedServer.Status.ONLINE:
        return {
            "ok": False,
            "response": Response(
                {
                    'error': (
                        f'Server {target_server.name} is {target_server.status}. '
                        'Only ONLINE remote servers can receive deployments.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            ),
        }

    return {
        "ok": True,
        "specified": True,
        "target_server": target_server,
        "target_is_local": False,
        "effective_server": target_server,
    }


_ENV_KEY_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_MASKED_SECRET_PATTERN = re.compile(r'^[\*\u2022]{4,}$')


def _is_valid_env_key(key: str) -> bool:
    """Return True when an env var key is in shell-safe format."""
    return bool(_ENV_KEY_PATTERN.match(str(key or '').strip()))


def _looks_masked_secret(value: str) -> bool:
    """
    Detect masked secret placeholders from UI payloads.
    Accepts repeated asterisks or bullet characters.
    """
    return bool(_MASKED_SECRET_PATTERN.match(str(value or '').strip()))


def is_remote_sync_request(request):
    """Check if the request is an authenticated inter-node remote sync request."""
    if not request or not hasattr(request, 'headers'):
        return False
    token = getattr(request, 'auth', None)
    authenticator = getattr(request, 'successful_authenticator', None)
    authenticator_name = authenticator.__class__.__name__ if authenticator else ''
    is_hmac_remote_sync = authenticator_name == 'RemoteSyncHMACAuthentication'
    is_api_token = hasattr(token, 'prefix')
    has_header = request.headers.get('X-SMSLY-Remote-Sync') == '1'
    return has_header and (is_api_token or is_hmac_remote_sync)


class ServiceViewSet(viewsets.ModelViewSet):
    """
    Service Management and Nested Resources.
    """
    queryset = Service.objects.all().order_by('-updated_at')
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['=name']
    # SECURITY (Batch H): throttles are applied PER ACTION below.
    # Class-level throttle_classes would cap *every* method (including
    # GETs that the dashboard fires when listing services, polling
    # health, fetching env_vars, etc.) at the deployment throttle
    # (3/min). The dashboard renders 4-20 GETs per page load and
    # 429s the user out of the gate. Safe methods (GET, HEAD, OPTIONS)
    # now fall through to the default user-rate throttle
    # (``'user': '5000/hour'`` in settings.py). Write actions
    # (deploy, restart, stop, bulk-action, etc.) declare their own
    # ``throttle_classes=[BurstRateThrottle, DeploymentRateThrottle]``
    # on the @action decorator to keep the deployment-burst guard.
    throttle_classes: list = []

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return self.queryset.none()
        if user.is_superuser or is_remote_sync_request(self.request):
            return self.queryset.all().select_related('project').prefetch_related('deployments')
        qs = self.queryset.filter(
            get_team_q_filter(user, request=self.request)
        )
        # Hide services that are being deleted or already deleted from list
        # view only — detail endpoints (retry-delete, force-purge) must still
        # resolve these by PK.
        if self.action == 'list':
            qs = qs.exclude(
                status__in=[Service.Status.DELETED, Service.Status.DELETION_PENDING]
            )
        return qs.select_related('project').prefetch_related('deployments')

    def _is_remote_sync_request(self):
        return is_remote_sync_request(self.request)

    def perform_create(self, serializer):
        project = serializer.validated_data.get('project')
        if project:
            assert_can_write(self.request.user, project, action='create service in')
        from .models_core import ManagedServer
        server = serializer.validated_data.get('server')

        # Seamless: If no server is assigned, default to the primary (local)
        # controller so user workloads land on the local server by default.
        if not server:
            server = ManagedServer.get_primary()
            if not server:
                server = ManagedServer.objects.filter(
                    status='ONLINE'
                ).order_by('?').first()
            if not server:
                # No ManagedServer exists at all — auto-register the local
                # controller so future service creations find it.
                server = _ensure_local_server_record()
            if server:
                logger.info("Auto-assigning server %s to service %s", server.name, serializer.validated_data.get('name'))
            else:
                logger.warning(
                    "No managed server available for service %s — "
                    "deployments will target the local controller",
                    serializer.validated_data.get('name'),
                )

        if server:
            ServerGuard.assert_user_workload_allowed(server)

        service = serializer.save(owner=self.request.user, server=server)

        # Setup provider webhook only for direct user actions.
        if (
            not self._is_remote_sync_request()
            and service.deploy_type == 'GIT'
            and service.repository_url
        ):
            threading.Thread(
                target=_setup_provider_webhook,
                args=(self.request.user, service.repository_url),
                daemon=True
            ).start()

    def perform_update(self, serializer):
        assert_can_write(self.request.user, serializer.instance)
        from .models_core import ManagedServer

        old_repo_url = serializer.instance.repository_url if serializer.instance else None

        if 'server' in serializer.validated_data:
            server = serializer.validated_data.get('server')
            if not server:
                server = ManagedServer.get_primary()
                if not server:
                    server = ManagedServer.objects.filter(
                        status='ONLINE'
                    ).order_by('?').first()
                if server:
                    logger.info("Auto-assigning server %s to service %s during update", server.name, serializer.instance.name)
            ServerGuard.assert_user_workload_allowed(server)
            service = serializer.save(server=server)
        else:
            service = serializer.save()

        # Setup provider webhook if repo URL changed
        new_repo_url = service.repository_url
        if (
            not self._is_remote_sync_request()
            and service.deploy_type == 'GIT'
            and new_repo_url
            and new_repo_url != old_repo_url
        ):
            threading.Thread(
                target=_setup_provider_webhook,
                args=(self.request.user, new_repo_url),
                daemon=True
            ).start()


    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        assert_can_delete(self.request.user, instance)
        force = _parse_bool(request.query_params.get('force'))
        if self._is_remote_sync_request():
            return self._destroy_remote_sync(instance, force=force)
        self.perform_destroy(instance, force=force)
        return Response(
            {
                "ok": True,
                "status": "deletion_pending",
                "message": "Deletion has started.",
                "resource_id": str(instance.id),
                "force": force
            },
            status=status.HTTP_202_ACCEPTED
        )

    def _destroy_remote_sync(self, instance, force=False):
        """
        Inter-node deletes must finish runtime cleanup before the controller
        removes its local record. Remote nodes may not have a local Celery
        worker, so queuing delete_service_task here can leave containers alive.
        """
        from .models_core import Service
        from .services.deletion_orchestrator import DeletionOrchestrator

        orchestrator = DeletionOrchestrator()
        success = orchestrator.delete_service_resources(instance, force=force)
        if success:
            service_id = str(instance.id)
            service_name = instance.name
            instance.delete()
            self._sync_caddy()
            return Response(
                {
                    "ok": True,
                    "status": "deleted",
                    "message": "Remote runtime resources were removed.",
                    "resource_id": service_id,
                    "service_name": service_name,
                    "force": force,
                },
                status=status.HTTP_200_OK,
            )

        instance.status = Service.Status.DELETION_FAILED
        instance.deletion_error = (
            "Remote runtime cleanup failed on this node; service was not deleted."
        )
        instance.save(update_fields=['status', 'deletion_error'])
        return Response(
            {
                "ok": False,
                "status": "deletion_failed",
                "error": instance.deletion_error,
                "resource_id": str(instance.id),
                "force": force,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    def perform_destroy(self, instance, force=False):
        """Set status to pending and queue async deletion."""
        from .models_core import Service
        from .tasks import delete_service_task

        instance.status = Service.Status.DELETION_PENDING
        instance.save(update_fields=['status'])

        from .utils import log_event
        log_event(
            actor=self.request.user.get_username(),
            action='SERVICE_DELETE_REQUESTED',
            target=f'Service: {instance.name}',
            metadata={
                'service_id': str(instance.id),
                'service_name': instance.name,
                'user_id': str(self.request.user.id),
                'ip': self.request.META.get('REMOTE_ADDR'),
                'force': force,
            },
        )

        if force:
            from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
            logger.info("Force-purging service %s from database synchronously.", instance.name)
            try:
                orchestrator = DeletionOrchestrator()
                orchestrator.delete_service_resources(instance, force=True)
            except Exception as exc:
                logger.warning("Resource cleanup failed during force-purge of %s: %s", instance.id, exc)
            try:
                instance.delete()
                logger.info("Force-purge complete for service %s.", instance.id)
            except Exception as exc:
                logger.error("Force-purge DB deletion failed for %s: %s", instance.id, exc)
            self._sync_caddy()
            return

        delete_service_task.delay(str(instance.id), force=force)
        self._sync_caddy()

    @action(detail=True, methods=['post'], url_path='retry-delete')
    def retry_delete(self, request, pk=None):
        instance = self.get_object()
        force = _parse_bool(request.data.get('force') or request.query_params.get('force'))
        from .models_core import Service
        if instance.status not in [Service.Status.DELETION_FAILED, Service.Status.DELETION_PENDING]:
            return Response({"error": "Service is not in a failed or pending deletion state."}, status=status.HTTP_400_BAD_REQUEST)

        instance.status = Service.Status.DELETION_PENDING
        instance.save(update_fields=['status'])

        if force:
            from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
            logger.info("Force-purging service %s via retry-delete.", instance.id)
            try:
                orchestrator = DeletionOrchestrator()
                orchestrator.delete_service_resources(instance, force=True)
            except Exception as exc:
                logger.warning("Resource cleanup failed during retry force-purge of %s: %s", instance.id, exc)
            try:
                instance.delete()
                logger.info("Force-purge complete for service %s via retry-delete.", instance.id)
            except Exception as exc:
                logger.error("Force-purge DB deletion failed for %s: %s", instance.id, exc)
            self._sync_caddy()
            return Response({"message": "Force-purge complete.", "force": force}, status=status.HTTP_200_OK)

        from .tasks import delete_service_task
        delete_service_task.delay(str(instance.id), force=force)

        return Response({"message": "Retry cleanup initiated.", "force": force}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"], url_path="hide-public-domain")
    def hide_public_domain(self, request, pk=None):
        service = self.get_object()
        if not service.public_domain:
            return Response({"error": "No public domain assigned."}, status=status.HTTP_400_BAD_REQUEST)
        service.public_domain_hidden = True
        service.save(update_fields=["public_domain_hidden"])
        # Sync routing to remove public domain block
        _ = self._sync_caddy()
        return Response({"message": "Public domain hidden", "public_domain_hidden": True})

    @action(detail=True, methods=["post"], url_path="unhide-public-domain")
    def unhide_public_domain(self, request, pk=None):
        service = self.get_object()
        if not service.public_domain:
            return Response({"error": "No public domain assigned."}, status=status.HTTP_400_BAD_REQUEST)
        service.public_domain_hidden = False
        service.save(update_fields=["public_domain_hidden"])
        _ = self._sync_caddy()
        return Response({"message": "Public domain unhidden", "public_domain_hidden": False})

    # --- Nested Resources: Deployments ---
    @action(detail=True, methods=['get'])
    def deployments(self, request, pk=None):
        service = self.get_object()
        deployments = service.deployments.all().order_by('-created_at')
        page = self.paginate_queryset(deployments)
        if page is not None:
            serializer = DeploymentSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = DeploymentSerializer(deployments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        """
        Stop a running service.
        POST /api/v1/services/{id}/stop/
        Cancels any active deployments and marks the service as stopped.
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)

        try:
            from apps.deployments.utils_target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target["server_obj"]

            if target["target_type"] in ("remote", "lite_agent") and active_server:
                from apps.deployments.services.remote_orchestrator import (
                    RemoteOrchestrator,
                )
                orchestrator = RemoteOrchestrator(active_server)
                remote_id = orchestrator._search_remote_service(service, "/api/v1/services/")
                if remote_id:
                    orchestrator._request(
                        method='POST',
                        path=f"/api/v1/services/{remote_id}/stop/",
                        timeout=15,
                    )
        except Exception as e:
            logger.warning("Stop resolution/remote call failed for service %s: %s", service.id, e)

        # Cancel any active/building deployments
        active_deployments = service.deployments.filter(
            status__in=[
                Deployment.Status.ACTIVE,
                Deployment.Status.BUILDING,
                Deployment.Status.DEPLOYING,
                Deployment.Status.HEALTH_CHECK,
                Deployment.Status.QUEUED,
                Deployment.Status.REVIEW,
            ]
        )
        count = active_deployments.update(
            status=Deployment.Status.CANCELLED,
            finished_at=timezone.now(),
        )

        # Log the stop action
        AuditLog(
            actor=request.user.get_username(),
            action='SERVICE_STOP',
            target=f'Service: {service.name}',
            metadata={'service_id': str(service.id), 'deployments_cancelled': count},
        ).save()

        return Response({
            'message': f'Service {service.name} stopped',
            'deployments_cancelled': count,
        })

    @action(detail=True, methods=['post'])
    def restart(self, request, pk=None):
        """
        Fast-restart a service by restarting its existing Docker container.
        POST /api/v1/services/{id}/restart/
        Body: { "force_rebuild": true } to trigger a full rebuild instead.

        Default behaviour (<5 seconds):
          1. Find the active deployment's container
          2. `docker restart` it
          3. Re-mark it ACTIVE

         With force_rebuild=true (full pipeline, minutes):
           1. Cancel active deployments
           2. Queue a fresh build + deploy
        """
        from django.db import transaction

        service = self.get_object()
        assert_can_write(self.request.user, service)
        force_rebuild = _parse_bool(request.data.get('force_rebuild', False))

        # Lock the service row to prevent concurrent restarts
        service = Service.objects.select_for_update().get(id=service.id)

        # Clear health monitor restart state (ends exponential backoff)
        from apps.deployments.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))

        # ── Fast restart path: just docker restart the container ──
        if not force_rebuild:
            try:
                from apps.deployments.utils_target import (
                    resolve_active_execution_target,
                )
                target = resolve_active_execution_target(service)
                active_server = target.get("server_obj")
                target_type = target.get("target_type")
                container_id = target.get("runtime_id")
            except Exception as e:
                logger.warning("Target resolution failed for restart, falling back to db: %s", e)
                active_deploy = service.deployments.filter(status=Deployment.Status.ACTIVE).order_by('-created_at').first()
                container_id = active_deploy.container_id if active_deploy else None
                target_type = "local"
                active_server = None

            if target_type in ("remote", "lite_agent") and active_server:
                try:
                    from apps.deployments.services.remote_orchestrator import (
                        RemoteOrchestrator,
                    )
                    orchestrator = RemoteOrchestrator(active_server)
                    remote_id = orchestrator._search_remote_service(service, "/api/v1/services/")
                    if not remote_id:
                        logger.warning("Remote service %s not found on %s for fast restart.", service.name, active_server.host)
                    else:
                        resp = orchestrator._request(
                            method='POST',
                            path=f"/api/v1/services/{remote_id}/restart/",
                            params={'force_rebuild': 'false'},
                            timeout=15,
                        )
                        if resp and resp.status_code in (200, 202):
                            # Set restart grace period in cache
                            from django.core.cache import cache
                            cache.set(f"health:restart_grace:{service.id}", True, timeout=60)
                            AuditLog(
                                actor=request.user.get_username(),
                                action='SERVICE_FAST_RESTART',
                                target=f'Service: {service.name}',
                                metadata={
                                    'service_id': str(service.id),
                                    'method': 'remote_docker_restart',
                                    'remote_server': active_server.host,
                                },
                            ).save()
                            return Response({
                                'message': f'Service {service.name} restarted (fast) remotely',
                                'method': 'remote_docker_restart',
                            })
                        logger.warning("Fast remote restart failed for %s. Falling back to full rebuild.", service.name)
                except Exception as exc:
                    logger.warning("Fast remote restart request failed: %s", exc)

            elif container_id:
                try:
                    import docker as docker_lib
                    client = docker_lib.from_env()
                    container = client.containers.get(container_id)
                    container.restart(timeout=10)

                    # Update health status
                    service.health_status = 'starting'
                    service.save(update_fields=['health_status', 'updated_at'])

                    # Set restart grace period so health monitor doesn't false-fail
                    from django.core.cache import cache
                    cache.set(f"health:restart_grace:{service.id}", True, timeout=60)

                    AuditLog(
                        actor=request.user.get_username(),
                        action='SERVICE_FAST_RESTART',
                        target=f'Service: {service.name}',
                        metadata={
                            'service_id': str(service.id),
                            'container_id': container_id,
                            'method': 'docker_restart',
                        },
                    ).save()

                    return Response({
                        'message': f'Service {service.name} restarted (fast)',
                        'method': 'docker_restart',
                        'container_id': container_id,
                    })
                except Exception as exc:
                    logger.warning(
                        "Fast restart failed for %s (container=%s): %s. "
                        "Falling back to full rebuild.",
                        service.name, container_id, exc,
                    )
                    # Fall through to full rebuild

        # ── Full rebuild path (wrapped in atomic for rollback) ──
        try:
            with transaction.atomic():
                service.deployments.filter(
                    status__in=[
                        Deployment.Status.ACTIVE,
                        Deployment.Status.BUILDING,
                        Deployment.Status.DEPLOYING,
                    ]
                ).update(
                    status=Deployment.Status.CANCELLED,
                    finished_at=timezone.now(),
                )

                provider = _resolve_provider_for_service(service)
                if not provider:
                    return Response({'error': 'No active cloud provider configured'},
                                    status=status.HTTP_400_BAD_REQUEST)

                deployment = Deployment.objects.create(
                    service=service,
                    status=Deployment.Status.QUEUED,
                    commit_hash='latest',
                    commit_message='Service restart (full rebuild)',
                    branch=service.branch or '',
                )
        except Exception as exc:
            logger.error("Failed to prepare full rebuild for %s: %s", service.name, exc)
            return Response({'error': f'Failed to queue rebuild: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=str(provider.id),
                               skip_review=True)

        AuditLog(
            actor=request.user.get_username(),
            action='SERVICE_RESTART',
            target=f'Service: {service.name}',
            metadata={'service_id': str(service.id), 'deployment_id': str(deployment.id)},
        ).save()

        return Response({
            'message': f'Service {service.name} restarting (full rebuild)',
            'method': 'full_rebuild',
            'deployment': DeploymentSerializer(deployment).data,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='recheck-health')
    def recheck_health(self, request, pk=None):
        """
        Trigger an immediate health re-check for one service.
        Useful when a route was temporarily unavailable and needs unpark.
        """
        service = self.get_object()
        reset_backoff = _parse_bool(request.data.get('reset_backoff', True))

        try:
            from apps.deployments.services.health_monitor import (
                _check_service_health,  # intentional internal call for immediate check
                reset_restart_state,
            )

            if reset_backoff:
                reset_restart_state(str(service.id))

            _check_service_health(service, Deployment)
            service.refresh_from_db(fields=['health_status', 'updated_at'])

            latest = (
                service.deployments
                .order_by('-created_at')
                .values_list('status', flat=True)
                .first()
            )
            return Response({
                'service_id': str(service.id),
                'health_status': service.health_status,
                'latest_deployment_status': latest,
                'backoff_reset': reset_backoff,
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Manual health recheck failed for %s: %s", service.id, exc)
            return Response(
                {'error': 'Failed to run health recheck'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['get'], url_path='status')
    def status(self, request, pk=None):
        """
        Return the local runtime state for this service on the current node.

        Controller-side remote deployment verification calls this endpoint on
        the agent after the agent reports ACTIVE.
        """
        service = self.get_object()

        try:
            import docker as docker_lib

            from apps.cloud.docker_client import get_docker_client

            client = get_docker_client()
            container = None
            try:
                container = client.containers.get(service.name)
            except docker_lib.errors.NotFound:
                candidates = client.containers.list(
                    all=True,
                    filters={'name': service.name},
                )
                for candidate in candidates:
                    if getattr(candidate, 'name', '') == service.name:
                        container = candidate
                        break
                if container is None and candidates:
                    container = candidates[0]

            if container is None:
                return Response({
                    'service_id': str(service.id),
                    'service_name': service.name,
                    'status': 'not_found',
                    'running': False,
                    'container_id': None,
                })

            container.reload()
            state = container.attrs.get('State') or {}
            runtime_status = (state.get('Status') or container.status or 'unknown').lower()
            health_status = ((state.get('Health') or {}).get('Status') or '').lower()
            effective_status = runtime_status
            if runtime_status == 'running' and health_status == 'unhealthy':
                effective_status = 'unhealthy'

            return Response({
                'service_id': str(service.id),
                'service_name': service.name,
                'status': effective_status,
                'running': runtime_status == 'running',
                'health': health_status or None,
                'container_id': container.id,
                'container_name': container.name,
                'image': ','.join(getattr(container.image, 'tags', []) or []),
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Service runtime status failed for %s: %s", service.id, exc)
            return Response(
                {
                    'service_id': str(service.id),
                    'service_name': service.name,
                    'status': 'unknown',
                    'running': False,
                    'error': str(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    @action(detail=True, methods=['post'])
    def deploy(self, request, pk=None):
        """
        Manually trigger deployment for a service.
        POST /api/v1/services/{id}/deploy/
        Body: {
            "ref": "commit_hash",
            "image_name": "registry:5000/...",
            "target_server_id": "uuid-or-null"
        }
        When target_server_id is omitted, deploy to the service's assigned node.
        When target_server_id is null/empty/"local" (or a primary server UUID),
        deploy this one run to the local controller.
        When target_server_id is a worker UUID, deploy to that specific node.
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)
        ref = request.data.get('ref', 'HEAD')
        is_remote_sync = self._is_remote_sync_request()
        requested_skip_review = _parse_bool(request.data.get('skip_review', False))
        skip_review = requested_skip_review if is_remote_sync else False
        source_node = str(request.data.get('source_node') or '').strip()
        image_name = str(request.data.get('image_name') or '').strip()

        if (source_node or image_name or requested_skip_review) and not is_remote_sync:
            return Response(
                {
                    'error': (
                        'source_node, image_name, and skip_review are reserved '
                        'for authenticated node-to-node deployment requests.'
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        target = _resolve_requested_deploy_target(request, service)
        if not target["ok"]:
            return target["response"]
        target_server = target["target_server"]
        target_is_local = target["target_is_local"]
        effective_server = target["effective_server"]

        guard = ServerGuard.check_user_workload_allowed(effective_server)
        if not guard["ok"]:
            return Response(guard, status=status.HTTP_400_BAD_REQUEST)

        # Prevent rapid-fire deployment spam
        # If this is a remote sync, the master has already created a tracking deployment
        # and we shouldn't block the remote worker from creating its execution deployment.
        existing = _has_active_deployment(service)
        if existing and not is_remote_sync:
            return Response({
                'error': f'Deployment already in progress (status: {existing.status}). '
                         'Wait for it to finish or cancel it first.',
                'existing_deployment': DeploymentSerializer(existing).data,
            }, status=status.HTTP_409_CONFLICT)

        # Determine provider
        provider = _resolve_provider_for_target(
            service,
            target_is_local=target_is_local,
        )
        if not provider:
            message = (
                'No active local cloud provider configured'
                if target_is_local
                else 'No active cloud provider configured'
            )
            return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)

        # For DOCKER type services triggered from a remote master, clear
        # source_node to prevent the task from re-delegating back.
        # For GIT with a pre-built image (build-agent optimization), keep
        # source_node so the task can distinguish master-triggered deploys
        # from user-triggered (and skip the build phase).
        is_docker_delegated = source_node and service.deploy_type == 'DOCKER'
        has_prebuilt = bool(source_node and image_name)

        if has_prebuilt and service.docker_image != image_name:
            service.docker_image = image_name
            service.save(update_fields=["docker_image"])

        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=ref if ref != 'HEAD' else 'latest',
            commit_message=f"Remote Deploy: {ref}" if source_node else f"Manual Trigger: {ref}",
            branch=service.branch or '',
            source_node=None if is_docker_delegated else source_node,
            target_server=target_server,
            target_is_local=target_is_local,
            queued_min_replicas=service.min_replicas,
        )

        try:
            enqueue_smart_deploy_task(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
                skip_review=skip_review
            )
        except Exception as exc:  # pragma: no cover - broker/runtime failure
            logger.exception(
                "Failed to enqueue deploy task for service=%s deployment=%s",
                service.id,
                deployment.id,
            )
            deployment.status = Deployment.Status.FAILED
            deployment.finished_at = timezone.now()
            deployment.build_logs = (
                (deployment.build_logs or '')
                + f"\n[ERROR] Failed to queue deployment task: {exc}\n"
            )
            deployment.save(
                update_fields=['status', 'finished_at', 'build_logs', 'updated_at']
            )
            return Response(
                {
                    'error': 'Failed to queue deployment task. Check Celery/Redis health.',
                    'deployment': DeploymentSerializer(deployment).data,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(DeploymentSerializer(deployment).data)

    @action(detail=True, methods=['post'], url_path='trigger-jules-fix')
    def trigger_jules_fix(self, request, pk=None):
        service = self.get_object()
        deployment_id = request.data.get('deployment_id')
        if deployment_id:
            try:
                deployment = Deployment.objects.get(id=deployment_id, service=service)
            except Deployment.DoesNotExist:
                return Response(
                    {'error': 'Deployment not found for this service.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            failed_statuses = [Deployment.Status.FAILED, Deployment.Status.BUILD_FAILED]
            deployment = Deployment.objects.filter(
                service=service, status__in=failed_statuses
            ).order_by('-created_at').first()
            if not deployment:
                return Response(
                    {'error': 'No failed deployment found for this service.'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            deployment_id = str(deployment.id)
        from apps.intelligence.jules_fix.jules_fix import jules_fix_deployment_failure
        jules_fix_deployment_failure.delay(
            deployment_id=str(deployment.id),
            logs=deployment.build_logs or "",
            repo_path="",
            repo_url=service.repository_url or "",
        )
        logger.info("Manual Jules auto-fix triggered for service=%s deployment=%s", service.id, deployment.id)
        AuditLog(
            actor=request.user.get_username(),
            action='TRIGGER_JULES_FIX',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'deployment_id': str(deployment.id),
            },
        ).save()
        return Response({
            'deployment_id': str(deployment.id),
            'message': f'Jules auto-fix triggered for deployment {deployment.id}.',
        })

    @action(detail=True, methods=['get'], url_path='incident-report')
    def incident_report(self, request, pk=None):
        """Aggregate incident timeline from all relevant data sources.

        GET /api/v1/services/{id}/incident-report/

        Returns a consolidated timeline covering deployments, health
        transitions, resource alerts, auto-rollbacks, backup operations,
        AI remediation, scaling events, transfer failures, snapshots,
        routing failures, mesh/network incidents, cloud upload failures,
        and CrowdSec WAF bans.
        """
        service = self.get_object()
        from apps.deployments.models_audit import AuditLog
        from apps.deployments.models_backup import ServiceBackup, ServiceSnapshot
        from apps.deployments.models_transfer import ServerTransfer
        from apps.notifications.models import ResourceAlert

        events: list = []

        # ── 1. All non-success deployments (last 90 days) ─────────────
        from apps.deployments.models_core import Deployment
        failure_statuses = [
            'FAILED', 'CANCELLED', 'BUILD_FAILED', 'BACKUP_FAILED',
            'MIGRATION_FAILED', 'HEALTH_CHECK_FAILED', 'ROLLED_BACK',
        ]
        deploys = (
            Deployment.objects
            .filter(service=service)
            .order_by('-created_at')
        )
        for d in deploys[:30]:
            is_failure = d.status in failure_statuses
            events.append({
                'type': 'deployment',
                'severity': 'critical' if d.status == 'FAILED' else (
                    'warning' if is_failure else 'info'
                ),
                'timestamp': d.created_at.isoformat() if d.created_at else '',
                'title': f"Deployment {d.status.lower().replace('_', ' ')}",
                'detail': (d.commit_message or '')[:500],
                'deployment_id': str(d.id),
                'status': d.status,
                'branch': d.branch or '',
                'is_rollback': getattr(d, 'is_rollback', False),
            })

        # ── 2. Resource alerts ───────────────────────────────────────
        alerts = (
            ResourceAlert.objects
            .filter(service=service)
            .order_by('-created_at')[:20]
        )
        for a in alerts:
            events.append({
                'type': 'resource_alert',
                'severity': a.severity.lower(),
                'timestamp': a.created_at.isoformat() if a.created_at else '',
                'title': a.title or a.metric or 'Resource alert',
                'detail': a.message or '',
                'metric': a.metric or '',
                'threshold': getattr(a, 'threshold', None),
                'current_value': getattr(a, 'current_value', None),
                'acknowledged': a.acknowledged,
                'alert_id': str(a.id),
            })

        # ── 3. Health transitions (audit log) ─────────────────────────
        health_actions = [
            'HEALTH_TRANSITION', 'SERVICE_HEALTHY', 'SERVICE_UNHEALTHY',
            'HEALTH_WEBHOOK_APPLIED', 'HEALTH_WEBHOOK_REJECTED',
        ]
        health_audits = (
            AuditLog.objects
            .filter(
                action__in=health_actions,
                metadata__contains={'service_id': str(service.id)},
            )
            .order_by('-timestamp')[:15]
        )
        for a in health_audits:
            previous = (a.metadata or {}).get('previous', '')
            current = (a.metadata or {}).get('current', '')
            title = a.metadata.get('message', '') or (
                f'{previous} → {current}' if previous and current else a.action
            )
            events.append({
                'type': 'health',
                'severity': (
                    'critical' if a.action == 'SERVICE_UNHEALTHY' else
                    'warning' if a.action == 'HEALTH_TRANSITION' else
                    'info'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': title,
                'detail': a.metadata.get('detail', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 4. Auto-rollback events ───────────────────────────────────
        rollback_audits = (
            AuditLog.objects
            .filter(
                action__in=['AUTO_ROLLBACK_TRIGGERED', 'STUCK_ROLLBACK_DETECTED', 'DEPLOYMENT_ROLLBACK', 'DEPLOYMENT_ROLLBACK_INSTANT'],
                metadata__contains={'service_id': str(service.id)},
            )
            .order_by('-timestamp')[:10]
        )
        for a in rollback_audits:
            events.append({
                'type': 'rollback',
                'severity': 'critical' if 'STUCK' in a.action else 'warning',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 5. Backup operations (audit log + backup model) ───────────
        backup_audits = (
            AuditLog.objects
            .filter(
                action__in=[
                    'BACKUP_CREATE', 'BACKUP_RESTORE', 'BACKUP_INTEGRITY_CHECK',
                    'BACKUP_KEY_IMPORTED', 'BACKUP_CLOUD_UPLOAD_FAILED',
                ],
                target__icontains=service.name,
            )
            .order_by('-timestamp')[:10]
        )
        for a in backup_audits:
            events.append({
                'type': 'backup',
                'severity': 'warning' if 'FAILED' in a.action else 'info',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # Also check ServiceBackup failures
        failed_backups = (
            ServiceBackup.objects
            .filter(service=service, status='FAILED')
            .order_by('-created_at')[:5]
        )
        for b in failed_backups:
            events.append({
                'type': 'backup_failure',
                'severity': 'warning',
                'timestamp': b.created_at.isoformat() if b.created_at else '',
                'title': 'Backup failed',
                'detail': b.error_message or '',
                'backup_id': str(b.id),
                'backup_type': b.backup_type,
            })

        # ── 6. AI remediation events ──────────────────────────────────
        ai_audits = (
            AuditLog.objects
            .filter(
                action__in=['SCALE_UP', 'DIAGNOSE', 'DIAGNOSIS', 'CLEANUP', 'TRIGGER_JULES_FIX'],
                metadata__contains={'service_id': str(service.id)},
            )
            .order_by('-timestamp')[:10]
        )
        for a in ai_audits:
            events.append({
                'type': 'ai_remediation',
                'severity': (
                    'warning' if a.action == 'SCALE_UP' else 'info'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 7. Service lifecycle events ───────────────────────────────
        lifecycle_audits = (
            AuditLog.objects
            .filter(
                action__in=[
                    'SERVICE_STOP', 'SERVICE_RESTART', 'SERVICE_FAST_RESTART',
                    'SERVICE_CREATE', 'SERVICE_DELETE_REQUESTED',
                ],
                target__icontains=service.name,
            )
            .order_by('-timestamp')[:10]
        )
        for a in lifecycle_audits:
            events.append({
                'type': 'service_lifecycle',
                'severity': 'info',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 8. Server transfers ───────────────────────────────────────
        transfers = (
            ServerTransfer.objects
            .filter(service=service)
            .exclude(status='COMPLETED')
            .order_by('-created_at')[:5]
        )
        for t in transfers:
            events.append({
                'type': 'transfer',
                'severity': 'critical' if t.status == 'FAILED' else 'warning',
                'timestamp': t.created_at.isoformat() if t.created_at else '',
                'title': f"Server transfer {t.status.lower()}",
                'detail': t.error_message or '',
                'transfer_id': str(t.id),
                'status': t.status,
            })

        # ── 9. Snapshots ──────────────────────────────────────────────
        snapshots = (
            ServiceSnapshot.objects
            .filter(service=service)
            .order_by('-created_at')[:10]
        )
        for s in snapshots:
            events.append({
                'type': 'snapshot',
                'severity': 'info',
                'timestamp': s.created_at.isoformat() if s.created_at else '',
                'title': s.label or f'Snapshot {s.id}',
                'detail': f'Trigger: {s.trigger}',
                'snapshot_id': str(s.id),
                'trigger': s.trigger,
            })

        # ── 10. Routing / infrastructure events ──────────────────────
        infra_audits = (
            AuditLog.objects
            .filter(
                action__in=['CADDY_RELOAD'],
            )
            .order_by('-timestamp')[:5]
        )
        for a in infra_audits:
            events.append({
                'type': 'infrastructure',
                'severity': 'info',
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': 'Caddy reload',
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
            })

        # ── 11. Mesh / WireGuard network events ──────────────────────
        mesh_audits = (
            AuditLog.objects
            .filter(
                action__in=[
                    'MESH_PEER_UNREACHABLE', 'MESH_DEPLOY_FAILED',
                    'MESH_DEPLOY_SUCCESS',
                ],
            )
            .order_by('-timestamp')[:5]
        )
        for a in mesh_audits:
            events.append({
                'type': 'mesh',
                'severity': (
                    'critical' if 'UNREACHABLE' in a.action or 'FAILED' in a.action
                    else 'info'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 12. Cloud / object storage upload failures ────────────────
        cloud_failures = (
            ServiceBackup.objects
            .filter(
                service=service,
                metadata__has_key='cloud_upload_error',
            )
            .order_by('-created_at')[:5]
        )
        for b in cloud_failures:
            events.append({
                'type': 'cloud_upload_failure',
                'severity': 'warning',
                'timestamp': b.created_at.isoformat() if b.created_at else '',
                'title': 'Cloud backup upload failed',
                'detail': (b.metadata or {}).get('cloud_upload_error', ''),
                'backup_id': str(b.id),
            })

        # ── 13. CrowdSec WAF summary ──────────────────────────────────
        try:
            import subprocess
            bans_result = subprocess.run(
                ['docker', 'exec', 'smsly-crowdsec',
                 'cscli', 'decisions', 'list', '-o', 'json'],
                capture_output=True, text=True, timeout=10,
            )
            if bans_result.returncode == 0:
                ban_count = 0
                try:
                    import json
                    bans = json.loads(bans_result.stdout)
                    ban_count = len(bans) if isinstance(bans, list) else 0
                except Exception:
                    pass
                events.append({
                    'type': 'waf_summary',
                    'severity': 'warning' if ban_count > 50 else 'info',
                    'timestamp': '',
                    'title': f'{ban_count} active WAF bans',
                    'detail': 'CrowdSec decisions currently enforcing',
                })
        except Exception:
            pass

        # ── Sort & return ─────────────────────────────────────────────
        events.sort(key=lambda e: e['timestamp'] or '', reverse=True)

        # Summary counts
        severity_counts = {'critical': 0, 'warning': 0, 'info': 0}
        for e in events:
            sev = e.get('severity', 'info')
            if sev in severity_counts:
                severity_counts[sev] += 1

        return Response({
            'service_id': str(service.id),
            'service_name': service.name,
            'total_events': len(events),
            'critical': severity_counts['critical'],
            'warning': severity_counts['warning'],
            'info': severity_counts['info'],
            'events': events,
        })

    # ── Preview Environments ─────────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='create-preview')
    def create_preview(self, request, pk=None):
        """
        Create a preview environment from a specific branch/PR.
        POST /api/v1/services/{id}/create-preview/
        Body: { "branch": "feature/login", "pr_number": 42 }

        Clones the parent service config, sets is_preview=True, deploys
        on the specified branch with a unique subdomain.
        """
        parent = self.get_object()
        branch = request.data.get('branch') or request.data.get('branch_name')
        pr_number = request.data.get('pr_number')

        if not branch:
            logger.warning("create_preview: missing branch, service=%s", parent.id)
            return Response(
                {'error': 'branch is required'},
                status=status.HTTP_400_BAD_REQUEST)

        # Check for existing preview on same branch
        existing = Service.objects.filter(
            parent_service=parent, branch=branch, is_preview=True
        ).first()
        if existing:
            logger.info("create_preview: preview already exists for service=%s branch=%s preview_id=%s", parent.id, branch, existing.id)
            return Response({
                'error': f'Preview already exists for branch "{branch}"',
                'preview_id': str(existing.id),
                'preview_url': existing.service_url,
            }, status=status.HTTP_409_CONFLICT)

        # Build preview name: pr-42-myservice or preview-feature-login-myservice
        slug_branch = re.sub(r'[^a-z0-9]+', '-', branch.lower()).strip('-')[:30]
        if pr_number:
            preview_name_base = f"pr-{pr_number}-{parent.name}"
        else:
            preview_name_base = f"preview-{slug_branch}-{parent.name}"

        # Leave room for counter suffix to avoid infinite loop when base is 255 chars
        preview_name_base = preview_name_base[:240]
        preview_name = preview_name_base
        counter = 1
        while Service.objects.filter(name=preview_name).exists():
            preview_name = f"{preview_name_base[:250]}-{counter}"
            counter += 1

        try:
            with transaction.atomic():
                preview = Service.objects.create(
                    name=preview_name,
                    repository_url=parent.repository_url,
                    branch=branch,
                    deploy_type=parent.deploy_type,
                    buildpack=parent.buildpack,
                    docker_image=parent.docker_image,
                    owner=parent.owner,
                    project=parent.project,
                    provider=parent.provider,
                    build_command=parent.build_command,
                    start_command=parent.start_command,
                    root_directory=parent.root_directory,
                    internal_port=parent.internal_port,
                    cpu_cores=parent.cpu_cores,
                    memory_mb=parent.memory_mb,
                    health_check_path=parent.health_check_path,
                    health_check_interval=parent.health_check_interval,
                    health_check_timeout=parent.health_check_timeout,
                    health_check_retries=parent.health_check_retries,
                    restart_policy=parent.restart_policy,
                    deploy_mode=parent.deploy_mode,
                    compose_file=parent.compose_file,
                    compose_main_service=parent.compose_main_service,
                    is_preview=True,
                    parent_service=parent,
                    pr_number=pr_number,
                )

                # Option A enterprise preview: clean start, do not copy parent env vars to prevent leaks and blast radius

                # Create and trigger deployment
                deployment = Deployment.objects.create(
                    service=preview,
                    status=Deployment.Status.QUEUED,
                    commit_hash='HEAD',
                    commit_message=f"Preview deploy: {branch}"
                    + (f" (PR #{pr_number})" if pr_number else ""),
                    branch=branch or '',
                )

                provider = _resolve_provider_for_service(preview)
                if provider:
                    smart_deploy_task.delay(
                        deployment_id=str(deployment.id), provider_id=str(provider.id))

        except (IntegrityError, ValidationError) as exc:
            logger.error("create_preview failed for service=%s branch=%s: %s", parent.id, branch, exc)
            return Response(
                {'error': f'Failed to create preview: {exc!s}'},
                status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'preview': ServiceSerializer(preview).data,
            'deployment': DeploymentSerializer(deployment).data,
            'preview_url': preview.service_url,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='legacy-previews')
    def list_previews(self, request, pk=None):
        """
        List all preview environments for a service.
        GET /api/v1/services/{id}/previews/
        """
        parent = self.get_object()
        previews = Service.objects.filter(
            parent_service=parent, is_preview=True
        ).order_by('-created_at').prefetch_related(
            models.Prefetch('deployments', queryset=Deployment.objects.order_by('-created_at'))
        )

        data = []
        for preview in previews:
            deploys = list(preview.deployments.all()[:1])
            latest_deploy = deploys[0] if deploys else None
            data.append({
                'id': str(preview.id),
                'service': str(parent.id),
                'name': preview.name,
                'branch': preview.branch,
                'branch_name': preview.branch,
                'commit_sha': latest_deploy.commit_hash if latest_deploy else '',
                'pr_number': preview.pr_number,
                'preview_url': preview.service_url,
                'health_status': preview.health_status,
                'status': latest_deploy.status if latest_deploy else (preview.health_status or 'UNKNOWN'),
                'created_at': preview.created_at.isoformat(),
                'updated_at': preview.updated_at.isoformat() if hasattr(preview, 'updated_at') and preview.updated_at else preview.created_at.isoformat(),
                'latest_deployment': {
                    'id': str(latest_deploy.id),
                    'status': latest_deploy.status,
                    'created_at': latest_deploy.created_at.isoformat(),
                } if latest_deploy else None,
            })

        return Response({'count': len(data), 'results': data})

    @action(detail=True, methods=['delete', 'post'], url_path='destroy-preview')
    def destroy_preview(self, request, pk=None):
        """
        Destroy a preview environment.
        DELETE/POST /api/v1/services/{id}/destroy-preview/
        Body: { "preview_id": "uuid" }
        """
        parent = self.get_object()
        preview_id = request.data.get('preview_id') or request.query_params.get('preview_id')

        if not preview_id:
            return Response(
                {'error': 'preview_id is required'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            preview = Service.objects.get(
                id=preview_id, parent_service=parent, is_preview=True)
        except Service.DoesNotExist:
            return Response(
                {'error': 'Preview not found'},
                status=status.HTTP_404_NOT_FOUND)

        # Stop the container if running
        try:
            provider = _resolve_provider_for_service(preview)
            if provider:
                from apps.cloud.adapter import get_adapter
                adapter = get_adapter(provider)
                last_deploy = preview.deployments.filter(
                    status=Deployment.Status.ACTIVE
                ).first()
                if last_deploy and last_deploy.container_id:
                    adapter.stop_container(last_deploy.container_id)
        except Exception:
            logger.warning("Could not stop preview container for %s", preview_id)

        preview_name = preview.name
        preview.delete()

        return Response({
            'message': f'Preview "{preview_name}" destroyed',
        })

    @action(detail=True, methods=['post'], url_path='multi-deploy')
    def multi_deploy(self, request, pk=None):
        """
        Deploy a service to the local server AND selected Grid servers.
        POST /api/v1/services/{id}/multi-deploy/
        Body: {
            "ref": "HEAD",
            "server_ids": ["uuid1", "uuid2"]
        }

        For each remote server:
        1. Check if a service with the same name exists
        2. If not, auto-create it (same repo_url, branch, buildpack, env vars)
        3. Trigger deploy on the remote server
        """
        from .models_servers import ManagedServer

        service = self.get_object()
        ref = str(request.data.get('ref', 'HEAD'))[:200]
        server_ids = request.data.get('server_ids', [])
        include_local = request.data.get('include_local', True)
        registry_url = str(request.data.get('registry_url', '')).strip()
        registry_username = str(request.data.get('registry_username', '')).strip()
        registry_password = str(request.data.get('registry_password', '')).strip()

        # F3: validate & cap server_ids
        if not isinstance(server_ids, list):
            return Response(
                {'error': 'server_ids must be a list'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(server_ids) > 20:
            return Response(
                {'error': 'Maximum 20 remote servers per deploy'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = {'local': None, 'remotes': []}

        # ── Custom registry → auto-create ephemeral project ──
        registry_override = None
        if registry_url:
            from .models_project import Project
            from .models_registry_scope import ScopedRegistry

            now_str = timezone.now().strftime('%Y%m%d-%H%M%S')
            new_project = Project.objects.create(
                owner=request.user,
                name=f"Deploy-{service.name}-{now_str}",
                description=f"Auto-created for custom registry deployment of {service.name}",
                is_ephemeral=True,
            )
            from django.contrib.contenttypes.models import ContentType
            ct = ContentType.objects.get_for_model(Project)
            ScopedRegistry.objects.create(
                content_type=ct,
                object_id=new_project.id,
                registry_url=registry_url,
                username=registry_username,
                password=registry_password,
            )
            old_project_id = str(service.project_id) if service.project_id else None
            service.project = new_project
            service.save(update_fields=['project', 'updated_at'])
            registry_override = {
                'url': registry_url,
                'project_id': str(new_project.id),
                'project_name': new_project.name,
                'old_project_id': old_project_id,
            }
            if registry_username:
                registry_override['username'] = registry_username
            if registry_password:
                registry_override['password'] = registry_password

        # ── 1. Local deploy ─────────────────────────────────────
        # Allow local deploy even if service is assigned to a remote
        # server — the user explicitly requested it via include_local=True.
        # The deployment will run on the master node regardless of the
        # service's current server assignment.
        if include_local:
            if ServerGuard.is_control_plane(getattr(service, 'server', None)):
                local_guard = ServerGuard.check_user_workload_allowed(getattr(service, 'server', None))
                results['local'] = {
                    'status': 'error',
                    'reason': local_guard['error']['message'],
                    'error': local_guard['error'],
                }
            else:
                existing = _has_active_deployment(service)
                if existing:
                    results['local'] = {
                        'status': 'skipped',
                        'reason': f'Deployment already in progress ({existing.status})',
                        'deployment': DeploymentSerializer(existing).data,
                    }
                else:
                    provider = _resolve_provider_for_service(service, prefer_local=True)
                    if not provider:
                        results['local'] = {
                            'status': 'error',
                            'reason': 'No active cloud provider configured',
                        }
                    else:
                        deployment = Deployment.objects.create(
                            service=service,
                            status=Deployment.Status.QUEUED,
                            commit_hash=ref if ref != 'HEAD' else 'latest',
                            commit_message=f"Multi-deploy: {ref}",
                            branch=service.branch or '',
                            target_is_local=True,
                            registry_override=registry_override,
                        )
                        try:
                            smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=str(provider.id))
                            results['local'] = {
                                'status': 'queued',
                                'deployment': DeploymentSerializer(deployment).data,
                            }
                        except Exception as exc:
                            logger.exception('multi_deploy: local deploy task failed')
                            deployment.status = Deployment.Status.FAILED
                            deployment.finished_at = timezone.now()
                            deployment.build_logs = f"\n[ERROR] {exc}\n"
                            deployment.save(
                                update_fields=['status', 'finished_at', 'build_logs', 'updated_at'])
                            results['local'] = {
                                'status': 'error',
                                'reason': 'Failed to queue local deployment. Check server logs.',
                                'deployment': DeploymentSerializer(deployment).data,
                            }
        else:
            results['local'] = {
                'status': 'skipped',
                'reason': 'Excluded by user preference',
            }
        # ── 2. Remote deploys ───────────────────────────────────
        if server_ids:
            servers = ManagedServer.objects.filter(
                id__in=server_ids,
                owner=request.user,
            )
            for server in servers:
                remote_result = {
                    'server_id': str(server.id),
                    'server_name': server.name,
                }
                remote_guard = ServerGuard.check_user_workload_allowed(server)
                if not remote_guard["ok"]:
                    remote_result['status'] = 'error'
                    remote_result['reason'] = remote_guard['error']['message']
                    remote_result['error'] = remote_guard['error']
                    results['remotes'].append(remote_result)
                    continue

                provider = _resolve_provider_for_target(service, target_is_local=False)
                if not provider:
                    remote_result['status'] = 'error'
                    remote_result['reason'] = 'No active cloud provider configured'
                    results['remotes'].append(remote_result)
                    continue

                # Create the local deployment tracking record on the Master
                deployment = Deployment.objects.create(
                    service=service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=ref if ref != 'HEAD' else 'latest',
                    commit_message=f"Multi-deploy: {ref}",
                    branch=service.branch or '',
                    target_server=server,
                    target_is_local=False,
                    registry_override=registry_override,
                )

                try:
                    smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=str(provider.id))
                    remote_result['status'] = 'queued'
                    remote_result['deployment'] = DeploymentSerializer(deployment).data
                except Exception as exc:
                    logger.exception('multi_deploy: remote deploy task failed')
                    deployment.status = Deployment.Status.FAILED
                    deployment.finished_at = timezone.now()
                    deployment.build_logs = f"\n[ERROR] {exc}\n"
                    deployment.save(
                        update_fields=['status', 'finished_at', 'build_logs', 'updated_at'])
                    remote_result['status'] = 'error'
                    remote_result['reason'] = 'Failed to queue remote deployment. Check server logs.'
                    remote_result['deployment'] = DeploymentSerializer(deployment).data

                results['remotes'].append(remote_result)

        return Response(results, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'], url_path='instant-rollback')
    def instant_rollback(self, request, pk=None):
        """
        Instantly rollback a service to its last successful deployment.
        POST /api/v1/services/{id}/instant-rollback/
        Body: { "confirm": true, "message": "optional reason" }

        This is the ONE-CLICK rollback that beats Railway.
        No need to find the deployment ID — just hit this endpoint.
        """
        # Enforce explicit confirmation (mirrors /deployments/{id}/rollback/)
        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return _error_response(
                "ROLLBACK_CONFIRMATION_REQUIRED",
                'Explicit confirmation required. Send "confirm": true.',
                user_action="Retry instant-rollback with confirm=true.",
                retryable=True,
            )

        service = self.get_object()
        guard = ServerGuard.check_user_workload_allowed(getattr(service, 'server', None))
        if not guard["ok"]:
            return Response(guard, status=status.HTTP_400_BAD_REQUEST)

        serializer = InstantRollbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get('message', '')

        # Find the most recent ACTIVE deployment
        last_good = (
            Deployment.objects
            .filter(service=service, status=Deployment.Status.ACTIVE)
            .order_by('-finished_at')
            .first()
        )

        if not last_good:
            return Response(
                {'error': 'No previous successful deployment to rollback to'},
                status=status.HTTP_404_NOT_FOUND)

        # Find the current (latest) deployment to mark as source
        current = (
            Deployment.objects
            .filter(service=service)
            .order_by('-created_at')
            .first()
        )

        # CRITICAL: refuse no-op rollbacks. If the latest deployment is the
        # same one we'd roll back to, there is no PRIOR good release to
        # revert to — surface a clear error instead of silently redeploying
        # the same commit/image.
        if current and current.id == last_good.id:
            return Response(
                {
                    'error': (
                        'No prior successful deployment to roll back to. '
                        'The most recent deployment is already the latest '
                        'active release.'
                    ),
                    'code': 'ROLLBACK_NOOP',
                    'deployment_id': str(current.id),
                    'commit_hash': current.commit_hash,
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Create rollback deployment
        rollback_msg = f"INSTANT ROLLBACK to {last_good.commit_hash[:7]}"
        if reason:
            rollback_msg += f" — {reason}"

        rollback_deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=last_good.commit_hash,
            commit_message=rollback_msg,
            branch=service.branch or '',
            is_rollback=True,
            rollback_from=current,
        )

        provider = _resolve_provider_for_service(service)
        if not provider:
            # Rollback was queued but there is no provider to run it on —
            # fail loudly so the client can attach a provider and retry.
            rollback_deployment.status = Deployment.Status.FAILED
            rollback_deployment.error_message = (
                'No active cloud provider available for this service.'
            )
            rollback_deployment.finished_at = timezone.now()
            rollback_deployment.save(
                update_fields=['status', 'error_message', 'finished_at', 'updated_at'],
            )
            return _error_response(
                "ROLLBACK_PERMISSION_DENIED",
                "No active provider available.",
                details={"service_id": str(service.id)},
                user_action="Attach an active provider to this service, then retry rollback.",
            )
        smart_deploy_task.delay(
            deployment_id=str(rollback_deployment.id), provider_id=str(provider.id))

        AuditLog(
            actor=request.user.get_username(),
            action='DEPLOYMENT_ROLLBACK_INSTANT',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'deployment_id': str(rollback_deployment.id),
                'rolled_back_to_id': str(last_good.id),
                'rolled_back_to_commit': last_good.commit_hash,
                'reason': reason,
            },
        ).save()

        return Response({
            'deployment': DeploymentSerializer(rollback_deployment).data,
            'rolled_back_to': DeploymentSerializer(last_good).data,
            'message': f'Rollback initiated to {last_good.commit_hash[:7]}',
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def timeline(self, request, pk=None):
        """
        Deployment timeline for a service — paginated, lightweight.
        GET /api/v1/services/{id}/timeline/
        Query params: ?status=ACTIVE&limit=20
        """
        service = self.get_object()
        deployments = service.deployments.all().order_by('-created_at')

        # Filter by status if requested
        status_filter = request.query_params.get('status')
        if status_filter:
            deployments = deployments.filter(status=status_filter.upper())

        page = self.paginate_queryset(deployments)
        if page is not None:
            serializer = DeploymentTimelineSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = DeploymentTimelineSerializer(deployments, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        """
        Deployment statistics for a service.
        GET /api/v1/services/{id}/stats/

        Returns: total deploys, success rate, avg duration, rollback count.
        """
        service = self.get_object()
        deploys = service.deployments.all()

        total = deploys.count()
        active = deploys.filter(status=Deployment.Status.ACTIVE).count()
        failed = deploys.filter(status=Deployment.Status.FAILED).count()
        rollbacks = deploys.filter(is_rollback=True).count()

        # Average duration of successful deployments
        successful = deploys.filter(
            status=Deployment.Status.ACTIVE,
            started_at__isnull=False,
            finished_at__isnull=False,
        ).annotate(
            duration=ExpressionWrapper(
                F('finished_at') - F('started_at'),
                output_field=DurationField()
            )
        ).aggregate(avg_duration=Avg('duration'))

        avg_seconds = None
        if successful['avg_duration']:
            avg_seconds = successful['avg_duration'].total_seconds()

        success_rate = (active / total * 100) if total > 0 else 0

        return Response({
            'total_deployments': total,
            'active': active,
            'failed': failed,
            'rollbacks': rollbacks,
            'success_rate': round(success_rate, 1),
            'avg_duration_seconds': round(avg_seconds, 1) if avg_seconds else None,
        })

    # --- Nested Resources: Environment Variables ---
    # NOTE: Keep GET and POST on a single @action. DRF collects actions via
    # `inspect.getmembers()` (sorted by name), which can register duplicate
    # url_path patterns in an unexpected order and cause 405s for valid methods.
    @action(detail=True, methods=['get', 'post'], url_path='env_vars')
    def env_vars(self, request, pk=None):
        service = self.get_object()
        reveal_secrets = not hasattr(getattr(request, 'auth', None), 'prefix')

        def _is_ciphertext(val: str) -> bool:
            """Detect Fernet ciphertext to prevent storing it as plaintext."""
            if not val or not isinstance(val, str):
                return False
            if val.startswith("gAAAA"):
                return True
            if len(val) > 100 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in val):
                try:
                    import base64
                    padded = val + '=' * (-len(val) % 4)
                    decoded = base64.urlsafe_b64decode(padded)
                    if len(decoded) >= 57 and decoded[0] == 0x80:
                        return True
                except Exception:
                    pass
            return False

        if request.method.upper() == 'GET':
            vars = service.env_vars.all().order_by('key')
            serializer = EnvVarSerializer(
                vars,
                many=True,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            )
            return Response(serializer.data)

        assert_can_write(self.request.user, service)
        payload_vars = request.data.get('vars')
        if payload_vars is not None:
            if not isinstance(payload_vars, list):
                return Response(
                    {'error': '"vars" must be a list of objects.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            normalized = []
            seen_keys = set()
            skipped_count = 0

            for idx, row in enumerate(payload_vars):
                if not isinstance(row, dict):
                    return Response(
                        {'error': f'Invalid item at index {idx}; expected object.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                key = str(row.get('key') or '').strip()
                if not key:
                    return Response(
                        {'error': f'Missing key at index {idx}.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if not _is_valid_env_key(key):
                    return Response(
                        {'error': f'Invalid environment variable key "{key}".'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if key in seen_keys:
                    return Response(
                        {'error': f'Duplicate key "{key}" in import payload.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                seen_keys.add(key)

                existing = EnvironmentVariable.objects.filter(
                    service=service, key=key).first()
                value = str(row.get('value', '') or '')
                if existing and existing.is_secret and _looks_masked_secret(value):
                    value = existing.value

                if _is_ciphertext(value):
                    logger.warning(
                        "[DB-ENCRYPT] Rejecting ciphertext env var %s for service %s — "
                        "sender sent undecrypted/double-encrypted data. "
                        "This var will NOT be saved to prevent corruption.",
                        key, service.name,
                    )
                    skipped_count += 1
                    continue

                if 'is_secret' in row:
                    is_secret = _parse_bool(row.get('is_secret'))
                else:
                    is_secret = bool(existing.is_secret) if existing else False

                normalized.append({
                    'key': key,
                    'value': value,
                    'is_secret': is_secret,
                })

            added = 0
            updated = 0
            try:
                with transaction.atomic():
                    for item in normalized:
                        _, created = EnvironmentVariable.objects.update_or_create(
                            service=service,
                            key=item['key'],
                            defaults={
                                'value': item['value'],
                                'is_secret': item['is_secret'],
                                'source': 'USER',
                            },
                        )
                        if created:
                            added += 1
                        else:
                            updated += 1
            except (ValidationError, DataError, IntegrityError) as exc:
                logger.warning(
                    "Invalid bulk env payload for service %s: %s",
                    service.id, exc,
                )
                return Response(
                    {'error': 'Invalid environment variable payload.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error(
                    "Failed bulk env upsert for service %s: %s", service.id, exc)
                return Response(
                    {'error': 'Failed to save environment variables'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            serializer = EnvVarSerializer(
                service.env_vars.all().order_by('key'),
                many=True,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            )
            resp_data = {
                'added': added,
                'updated': updated,
                'count': len(normalized),
                'env_vars': serializer.data,
            }
            if skipped_count > 0:
                resp_data['warning'] = f"Skipped {skipped_count} environment variables with ciphertext values."
            return Response(resp_data)

        # Allow partial data — key is required, value can be empty
        key = str(request.data.get('key') or '').strip()
        if not key:
            return Response(
                {'key': ['This field is required.']},
                status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_env_key(key):
            return Response(
                {'key': ['Use letters, numbers, and underscore; cannot start with a number.']},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = EnvironmentVariable.objects.filter(service=service, key=key).first()
        value = str(request.data.get('value', '') or '')
        if existing and existing.is_secret and _looks_masked_secret(value):
            value = existing.value
        if _is_ciphertext(value):
            return Response(
                {'value': ['Cannot save Fernet ciphertext as value. Sender must decrypt before sending.']},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if 'is_secret' in request.data:
            is_secret = _parse_bool(request.data.get('is_secret'))
        else:
            is_secret = bool(existing.is_secret) if existing else False

        is_locked = _parse_bool(request.data.get('is_locked', False))

        try:
            env_var, created = EnvironmentVariable.objects.update_or_create(
                service=service,
                key=key,
                defaults={'value': value, 'is_secret': is_secret, 'is_locked': is_locked, 'source': 'USER'},
            )
        except (ValidationError, DataError, IntegrityError) as exc:
            logger.warning("Invalid env var payload for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': f'Invalid environment variable payload for key "{key}"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Failed to save env var for service %s key=%s: %s", service.id, key, exc)
            return Response(
                {'error': 'Failed to save environment variable'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        out = EnvVarSerializer(
            env_var,
            context={'request': request, 'reveal_secrets': reveal_secrets},
        ).data
        return Response(
            out,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=True, methods=['get', 'delete', 'patch'],
            url_path='env_vars/(?P<var_id>\\d+)')
    def env_var_detail(self, request, pk=None, var_id=None):
        """GET / PATCH / DELETE on a single env var.

        The frontend ``getEnvVarValue`` (api.ts:591) calls
        ``GET /services/{id}/env_vars/{varId}/`` to reveal a
        secret. The previous decorator only allowed
        ``['delete', 'patch']`` which made the GET return 405
        and the secret-reveal flow silently fail.
        """
        service = self.get_object()
        try:
            var = EnvironmentVariable.objects.get(id=var_id, service=service)
        except EnvironmentVariable.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if request.method.upper() == 'GET':
            reveal_secrets = (
                request.user.is_superuser
                or var.service.owner_id == request.user.id
                or (getattr(request, 'auth', None)
                and hasattr(request.auth, 'prefix'))  # APIToken
            )
            return Response(
                EnvVarSerializer(
                    var,
                    context={'request': request, 'reveal_secrets': reveal_secrets},
                ).data
            )
        assert_can_write(self.request.user, service)
        if request.method.upper() == 'DELETE':
            var.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        # PATCH — toggle is_locked (or update any field)
        if 'is_locked' in request.data:
            var.is_locked = _parse_bool(request.data['is_locked'])
        if 'is_secret' in request.data:
            var.is_secret = _parse_bool(request.data['is_secret'])
        var.save()
        return Response(
            EnvVarSerializer(
                var,
                context={'request': request, 'reveal_secrets': reveal_secrets},
            ).data
        )

    @action(detail=True, methods=['get', 'post'], url_path='ai-router-config')
    def ai_router_config(self, request, pk=None):
        service = self.get_object()
        if not is_ai_router_service(service):
            return Response(
                {'error': 'This service is not an AI Router.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.method.upper() == 'GET':
            return Response(serialize_ai_router_config(service))

        raw_ids = request.data.get('selected_service_ids', [])
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list):
            return Response(
                {'error': '"selected_service_ids" must be a list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_base = str(
            request.data.get('api_base', DEFAULT_AI_ROUTER_API_BASE) or DEFAULT_AI_ROUTER_API_BASE
        ).strip() or DEFAULT_AI_ROUTER_API_BASE
        if not api_base.startswith('/'):
            api_base = f'/{api_base}'

        ui_base = str(
            request.data.get('ui_base', DEFAULT_AI_ROUTER_UI_BASE) or DEFAULT_AI_ROUTER_UI_BASE
        ).strip() or DEFAULT_AI_ROUTER_UI_BASE
        if not ui_base.startswith('/'):
            ui_base = f'/{ui_base}'

        braid_alias = str(
            request.data.get('braid_alias', DEFAULT_BRAID_ALIAS) or DEFAULT_BRAID_ALIAS
        ).strip() or DEFAULT_BRAID_ALIAS
        braid_enabled = _parse_bool(request.data.get('braid_enabled', True))

        persist_ai_router_config(
            service,
            selected_service_ids=[str(item).strip() for item in raw_ids],
            api_base=api_base,
            ui_base=ui_base,
            braid_alias=braid_alias,
            braid_enabled=braid_enabled,
        )
        service.refresh_from_db()
        return Response(serialize_ai_router_config(service))

    @action(detail=True, methods=['post'], url_path='verify-domain')
    def verify_domain(self, request, pk=None):
        """
        Verify that a custom domain's DNS points to this service's server.
        POST /api/v1/services/{id}/verify-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Per-apex daily cert issuance cap ────────────────────────
        # Mirrors the cap on check_domain so a single apex cannot exhaust
        # Let's Encrypt's rate-limit through repeated verifications.
        raw_domain_for_cap = domain.strip().lower()
        apex = (
            raw_domain_for_cap.split('.', 1)[-1]
            if '.' in raw_domain_for_cap
            else raw_domain_for_cap
        )
        if apex:
            cap_key = f"certs_issued:{apex}:{timezone.now().strftime('%Y%m%d')}"
            cap_value = cache.get(cap_key, 0)
            cap_limit = int(getattr(settings, 'CADDY_DAILY_CERT_CAP', 20))
            if cap_value >= cap_limit:
                logger.warning(
                    "verify_domain: daily cert cap reached for apex %s (%d)",
                    apex, cap_value,
                )
                return Response(
                    {
                        'error': (
                            f"Daily cert issuance cap reached for {apex}. "
                            "Try again tomorrow."
                        )
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            if cache.get(cap_key) is not None:
                try:
                    cache.incr(cap_key, 1)
                except ValueError:
                    cache.set(cap_key, cap_value + 1, timeout=86400)
            else:
                cache.set(cap_key, 1, timeout=86400)

        # Compare against the service's own public domain which already
        # resolves to the correct server IP. No hardcoded CNAME needed.
        cname_target = service.public_domain or ''
        if not cname_target:
            return Response({
                'domain': domain,
                'verified': False,
                'cname_target': '',
                'message': 'Service has no public domain assigned yet.',
            })

        from apps.domains.models import Domain, DomainStatus
        from apps.domains.verification import verify_custom_domain_dns

        domain_obj = Domain.objects.filter(service=service, domain_name=domain).first()
        transient_domain = domain_obj or Domain(domain_name=domain, service=service)
        old_status = domain_obj.status if domain_obj else DomainStatus.PENDING
        result = verify_custom_domain_dns(transient_domain, PlatformConfig.load())
        is_valid = result.verified

        if domain_obj:
            domain_obj.dns_expected = result.expected
            domain_obj.dns_actual = result.actual
            domain_obj.verified = is_valid
            domain_obj.last_error = None if is_valid else result.error
            if is_valid:
                domain_obj.status = (
                    old_status
                    if old_status in [DomainStatus.ACTIVE, DomainStatus.SSL_PROVISIONING]
                    else DomainStatus.DNS_VERIFIED
                )
            else:
                domain_obj.status = DomainStatus.DNS_PENDING
                domain_obj.ssl_active = False
            domain_obj.save(update_fields=[
                'status',
                'dns_expected',
                'dns_actual',
                'verified',
                'last_error',
                'ssl_active',
            ])
            if is_valid and old_status not in [
                DomainStatus.DNS_VERIFIED,
                DomainStatus.SSL_PROVISIONING,
                DomainStatus.ACTIVE,
            ]:
                self._sync_caddy()

        # ── Persist the verification result on the Service model ──
        # This is the critical step that was missing. Without this, the
        # domain_verified field stays False forever and the frontend badge
        # never updates.
        if is_valid and not service.domain_verified:
            service.domain_verified = True
            service.save(update_fields=['domain_verified'])
        elif not is_valid and service.domain_verified:
            service.domain_verified = False
            service.save(update_fields=['domain_verified'])

        from .utils import log_event
        log_event(
            actor=getattr(request.user, 'username', None) or 'system',
            action='DOMAIN_VERIFY',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'domain': domain,
                'result': 'success' if is_valid else 'fail',
            },
        )

        return Response({
            'domain': domain,
            'verified': is_valid,
            'cname_target': cname_target,
            'dns_expected': result.expected,
            'dns_actual': result.actual,
            'message': (
                'DNS verified! Domain points to Grid.'
                if is_valid
                else (
                    f'DNS not configured. Add {result.expected}. '
                    'Use DNS-only records so direct SSL can be issued.'
                )
            ),
        })

    def get_permissions(self):
        """Hardened auth for the Caddy ask endpoint: shared secret OR admin user."""
        if self.action == 'check_domain':
            return [CaddySecretOrAdminPermission()]
        return super().get_permissions()

    def get_authenticators(self):
        # Bypass DRF's default Token/Session authentication for the Caddy
        # on_demand_tls ask callback — Caddy calls this endpoint anonymously
        # and authenticates via the ``?secret=`` query param checked by
        # CaddySecretOrAdminPermission. Without this, every ask 401s before
        # the permission check can compare the secret.
        #
        # IMPORTANT: this is called by DRF's ``initialize_request`` BEFORE
        # ``dispatch`` sets ``self.action``, so we MUST key on the request
        # path (which is always available) and not on ``self.action``.
        if getattr(self, "request", None) is not None:
            p = self.request.path
            if p.endswith("/check-domain") or p.endswith("/check-domain/"):
                return []
        return super().get_authenticators()

    def get_throttles(self):
        """Throttle the Caddy ask endpoint to limit Let's Encrypt blast radius,
        and apply the deployment-burst guard only to write methods.

        The previous implementation let ``throttle_classes =
        [BurstRateThrottle, DeploymentRateThrottle]`` apply to every
        action on the viewset, including the GETs the dashboard fires
        on every page render. The dashboard renders 4-20 GETs per
        page; at 3/min the user is 429'd before the page can load.
        Now:
          - ``check_domain`` (Caddy) uses the ``caddy_ask`` scope.
          - GET / HEAD / OPTIONS fall through to the default user-rate
            throttle (``'user': '5000/hour'``).
          - POST / PUT / PATCH / DELETE get the deployment-burst
            guard.
        """
        if self.action == 'check_domain':
            from rest_framework.throttling import ScopedRateThrottle
            throttle = ScopedRateThrottle()
            throttle.scope = 'caddy_ask'
            self.throttle_scope = 'caddy_ask'
            return [throttle]
        if self.request.method in permissions.SAFE_METHODS:
            return []
        return [BurstRateThrottle(), DeploymentRateThrottle()]

    @action(detail=False, methods=['get'], url_path='check-domain')
    def check_domain(self, request):
        """
        Endpoint for Caddy's on_demand_tls 'ask' directive.
        GET /api/v1/services/check-domain/?domain=myapp.com
        Returns 200 OK if the domain is authorized, 404 otherwise.

        Authentication: requires ``X-Caddy-Secret`` header matching
        ``settings.CADDY_ASK_SECRET`` (machine-to-machine Caddy) OR an
        authenticated admin user. Rate-limited per IP via the ``caddy_ask``
        scope to prevent trivial DoS of Let's Encrypt.
        """
        # ── Per-apex daily cert issuance cap ────────────────────────
        # Limit blast radius if DNS verification is bypassed: a single
        # apex may not consume more than CADDY_DAILY_CERT_CAP (default 20)
        # hostnames per UTC day.
        domain = request.query_params.get('domain', '')
        if not domain:
            secret_val = request.query_params.get('secret', '')
            if '?domain=' in secret_val:
                domain = secret_val.split('?domain=')[-1]
        raw_domain_for_cap = domain.strip().lower()
        apex = (
            raw_domain_for_cap.split('.', 1)[-1]
            if '.' in raw_domain_for_cap
            else raw_domain_for_cap
        )
        if apex:
            cap_key = f"certs_issued:{apex}:{timezone.now().strftime('%Y%m%d')}"
            cap_value = cache.get(cap_key, 0)
            cap_limit = int(getattr(settings, 'CADDY_DAILY_CERT_CAP', 20))
            if cap_value >= cap_limit:
                logger.warning(
                    "check_domain: daily cert cap reached for apex %s (%d)",
                    apex, cap_value,
                )
                return Response(
                    {
                        'error': (
                            f"Daily cert issuance cap reached for {apex}. "
                            "Try again tomorrow."
                        )
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            if cache.get(cap_key) is not None:
                try:
                    cache.incr(cap_key, 1)
                except ValueError:
                    cache.set(cap_key, cap_value + 1, timeout=86400)
            else:
                cache.set(cap_key, 1, timeout=86400)

        raw_domain = raw_domain_for_cap
        if not raw_domain:
            return Response(status=status.HTTP_404_NOT_FOUND)
        import ipaddress
        is_ip = False
        try:
            ipaddress.ip_address(raw_domain)
            is_ip = True
            domain = raw_domain
        except ValueError:
            try:
                domain = normalize_domain(raw_domain)
            except ValueError:
                return Response(status=status.HTTP_404_NOT_FOUND)

        # 1. Check against PlatformConfig primary domain
        try:
            cfg = PlatformConfig.load()
            if cfg.domain and domain == cfg.domain.strip().lower():
                return Response(status=status.HTTP_200_OK)
        except Exception as exc:
            logger.debug("check_domain: PlatformConfig check failed: %s", exc)

        # 2. Check against Managed Servers (allow inter-node control traffic)
        from .models_core import ManagedServer
        query = Q(host=domain)
        if is_ip:
            query |= Q(private_ip=domain)

        if ManagedServer.objects.filter(query).exists():
            return Response(status=status.HTTP_200_OK)

        # 3. Check against Services (Public Domain)
        if Service.objects.filter(public_domain=domain).exists():
            return Response(status=status.HTTP_200_OK)

        # 3. Check verified custom domains. Pending JSONField entries are
        # intentionally not authorized, otherwise Caddy may attempt ACME before
        # the customer has pointed DNS at this server.
        from apps.domains.models import Domain, DomainStatus
        routable_custom_domain = (
            Domain.objects
            .filter(
                domain_name=domain,
                status__in=[
                    DomainStatus.ACTIVE,
                    DomainStatus.DNS_VERIFIED,
                    DomainStatus.SSL_PROVISIONING,
                ],
            )
            .filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE))
            .exists()
        )
        if routable_custom_domain:
            return Response(status=status.HTTP_200_OK)

        # 4. Check against Addons
        from .models_addons import Addon
        if Addon.objects.filter(public_domain=domain).exists():
            return Response(status=status.HTTP_200_OK)

        logger.warning("check_domain: unauthorized domain attempt: %s", domain)
        return Response(status=status.HTTP_404_NOT_FOUND)

    # ---------------------------------------------------------------------
    # Dependency graph endpoint – returns a list of services this service
    # depends on (by repo key) and a list of dependents.  The frontend can use
    # this to render a DAG.
    # ---------------------------------------------------------------------
    @action(detail=True, methods=['get'], url_path='dependencies', permission_classes=[permissions.IsAuthenticated])
    def dependencies(self, request, pk=None):
        try:
            service = self.get_queryset().get(id=pk)
        except Service.DoesNotExist:
            return Response({"error": "Service not found"}, status=status.HTTP_404_NOT_FOUND)

        # Build a simple dependency map using the same logic as the ecosystem
        # planner – we reuse the helper that extracts ``depends_on`` from the
        # stored plan (if any).  For simplicity we look at the Service model's
        # ``plan`` JSONField (assumed to exist) and read ``depends_on``.
        plan = getattr(service, 'plan', {}) or {}
        raw_deps = plan.get('depends_on', [])
        deps = []
        for token in raw_deps:
            # Resolve token to a Service if possible — only surface
            # services the caller can access.
            try:
                dep_svc = self.get_queryset().filter(name__iexact=token).first()
                if dep_svc:
                    deps.append({"id": str(dep_svc.id), "name": dep_svc.name})
            except Exception:
                continue

        # Find dependents (services that list this one in their depends_on)
        # and only include those the caller can access.
        dependents_qs = self.get_queryset().filter(plan__contains={"depends_on": [service.name]})
        dependents = [{"id": str(s.id), "name": s.name} for s in dependents_qs]

        return Response({"service": {"id": str(service.id), "name": service.name}, "depends_on": deps, "dependents": dependents})

    # ---------------------------------------------------------------------
    # Bulk actions – deploy, cancel, or run AI Senate on multiple services.
    # Expected payload: {"ids": ["uuid1", "uuid2"], "action": "deploy"}
    # ---------------------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='bulk-action', permission_classes=[permissions.IsAuthenticated])
    def bulk_action(self, request):
        ids = request.data.get('ids', [])
        action = request.data.get('action')
        if not isinstance(ids, list) or not action:
            return Response({"error": "Invalid payload"}, status=status.HTTP_400_BAD_REQUEST)

        # SECURITY: Scope the bulk action to services the caller can access
        # via get_queryset(). Otherwise any authenticated user could trigger
        # deploy/cancel/senate against other tenants' services.
        # SECURITY (Issue 25): wrap the iteration in a transaction
        # and use select_for_update so a service cannot be deleted
        # by another request between the filter and the action.
        # ``action == 'deploy'`` enqueues a Celery task — that work
        # is outside the DB transaction by design (the row lock is
        # released as soon as the task id is handed to the broker).
        results = []
        with transaction.atomic():
            services_qs = self.get_queryset().filter(id__in=ids).select_for_update()
            for svc in services_qs:
                try:
                    if action == 'deploy':
                        # Queue a smart_deploy_task for each service via smart deployment queue
                        from apps.deployments.models import Deployment
                        from apps.deployments.tasks_deploy import enqueue_smart_deploy_task
                        dep = Deployment.objects.create(service=svc, status=Deployment.Status.QUEUED, commit_message="Bulk deploy action")
                        enqueue_smart_deploy_task(str(dep.id), str(svc.provider.id) if svc.provider else None)
                    elif action == 'cancel':
                        # Cancel any queued or building deployments
                        from apps.deployments.models import Deployment
                        Deployment.objects.filter(service=svc, status__in=[Deployment.Status.QUEUED, Deployment.Status.BUILDING]).update(status=Deployment.Status.CANCELLED)
                    elif action == 'senate':
                        # Trigger AI Senate env enrichment using enhanced apply_intelligence_to_service
                        from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService
                        EnvironmentIntelligenceService.apply_intelligence_to_service(svc, scan_results={})
                    results.append({"id": str(svc.id), "status": "ok"})
                except Exception as exc:
                    logger.error("Bulk action %s failed for service %s: %s", action, svc.id, exc)
                    results.append({"id": str(svc.id), "status": "error", "error": str(exc)})
        return Response({"action": action, "results": results})

    # ---------------------------------------------------------------------
    # Sidebar summary endpoint – lightweight data for the UI project sidebar
    # ---------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='sidebar', permission_classes=[permissions.IsAuthenticated])
    def sidebar(self, request):
        """Return a minimal hierarchy of projects → repos for the UI.

        The current data model does not have an explicit ``Project`` entity,
        so we infer a project name from the ``Service.project`` attribute if it
        exists, otherwise we fall back to the repository owner (the part before
        the first ``/`` in ``repo``).  Each entry contains:

        ``project`` – string
        ``repos`` – list of ``{id, name, status}``
        """
        from collections import defaultdict
        result = defaultdict(list)
        # SECURITY: scope to the caller's accessible services via get_queryset()
        # so the sidebar cannot be used to enumerate other tenants' services.
        # Limit to 200 services to prevent unbounded queries.
        for svc in self.get_queryset()[:200]:
            # ``svc.repo`` is stored as a full URL in the model; we only need the
            # owner/repo slug for display.
            repo_slug = svc.repository_url.split('/')[-1] if svc.repository_url else str(svc.id)
            project_name = getattr(svc, 'project', None) or repo_slug.split('_')[0]
            result[project_name].append({
                'id': str(svc.id),
                'name': repo_slug,
                'status': svc.status.lower() if hasattr(svc, 'status') else 'unknown',
            })
        # Convert defaultdict to plain list for JSON serialization
        payload = [{'project': k, 'repos': v} for k, v in result.items()]
        return Response(payload)

    def _sync_caddy(self):
        """Regenerate Caddyfile with all custom domains and trigger reload."""
        try:
            from services.caddy_manager import apply_caddyfile, generate_caddyfile

            from .models import PlatformConfig
            from .utils import log_event
            config = PlatformConfig.load()
            content = generate_caddyfile(config)
            cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()
            result = apply_caddyfile(content, cloudflare_token=cf_token)
            if result['ok']:
                logger.info("Caddy synced after domain change")
                log_event(
                    action='CADDY_RELOAD',
                    actor='system',
                    target='caddy',
                    metadata={'ok': True, 'message': str(result.get('message', ''))[:200]},
                )
            else:
                logger.error("Caddy sync failed: %s", result['message'])
                log_event(
                    action='CADDY_RELOAD',
                    actor='system',
                    target='caddy',
                    metadata={'ok': False, 'message': str(result.get('message', ''))[:200]},
                )
            return {
                "ok": bool(result.get("ok")),
                "message": str(result.get("message", "")).strip(),
            }
        except Exception as e:
            logger.error("Caddy sync error: %s", e)
            return {
                "ok": False,
                "message": str(e),
            }

    def _find_domain_conflict(self, service: Service, domain: str):
        """Return conflicting service if domain is already assigned globally."""
        public_conflict = (
            Service.objects
            .exclude(id=service.id)
            .filter(public_domain=domain)
            .only("id", "name", "public_domain")
            .first()
        )
        if public_conflict:
            return public_conflict

        from apps.domains.models import Domain
        domain_obj = Domain.objects.filter(domain_name=domain).exclude(service=service).first()
        if domain_obj:
            return domain_obj.service
        return None

    def _enforce_custom_domain_quota(self, service: Service, new_total: int):
        """
        Enforce billing plan limit for custom domains.
        (Disabled for self-hosted instances).
        """
        if _check_tier_gates_disabled():
            return None
        try:
            from apps.billing.models import UserSubscription
            sub = UserSubscription.objects.filter(user=service.owner, status='ACTIVE').first()
            limit = sub.plan.max_custom_domains if sub and sub.plan else 1
            if new_total > limit:
                return Response(
                    {'error': f'Custom domain limit reached ({limit}). Please upgrade your plan.'},
                    status=status.HTTP_403_FORBIDDEN
                )
        except ImportError:
            pass
        return None

    @action(detail=True, methods=['post'], url_path='retry-domain')
    def retry_domain(self, request, pk=None):
        """Retry domain verification"""
        service = self.get_object()
        assert_can_write(request.user, service, action='retry domain verification')
        domain_name = request.data.get('domain', '').strip().lower()
        if not domain_name:
            return Response({'error': 'Domain required'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.domains.models import Domain
        domain_obj = Domain.objects.filter(service=service, domain_name=domain_name).first()
        if not domain_obj:
            return Response({'error': 'Domain not found'}, status=status.HTTP_404_NOT_FOUND)

        from apps.domains.tasks import verify_dns_and_provision_ssl_task
        verify_dns_and_provision_ssl_task.delay(domain_obj.id)

        return Response({'message': 'Verification retried', 'status': domain_obj.status})

    @action(detail=True, methods=['post'], url_path='add-domain')
    def add_domain(self, request, pk=None):
        """
        Add a custom domain to the service.
        POST /api/v1/services/{id}/add-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domains = [
            d for d in (service.custom_domains or [])
            if isinstance(d, str) and d.strip()
        ]
        if domain in domains:
            return Response({'error': 'Domain already added'},
                            status=status.HTTP_400_BAD_REQUEST)

        conflict = self._find_domain_conflict(service, domain)
        if conflict:
            return Response(
                {
                    'error': (
                        f'Domain already assigned to service "{conflict.name}". '
                        'A domain can only be attached to one service.'
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        domains = list(dict.fromkeys([*domains, domain]))
        quota_response = self._enforce_custom_domain_quota(service, len(domains))
        if quota_response is not None:
            return quota_response

        service.custom_domains = domains
        service.save(update_fields=['custom_domains'])

        from apps.domains.models import Domain, DomainStatus
        # Clean up old domains
        Domain.objects.filter(service=service).exclude(domain_name__in=domains).delete()

        # Add new domains
        for d in domains:
            domain_obj, created = Domain.objects.get_or_create(
                domain_name=d,
                defaults={'service': service, 'status': DomainStatus.PENDING}
            )
            if created:
                from apps.domains.tasks import verify_dns_and_provision_ssl_task
                verify_dns_and_provision_ssl_task.delay(domain_obj.id)


        cfg = PlatformConfig.load()
        cname_target = service.public_domain or cfg.domain or ''
        server_ip = str(cfg.server_ip or '')

        # Auto-sync Caddyfile so SSL + routing are provisioned immediately.
        # No service redeploy is required.
        caddy_result = self._sync_caddy()
        caddy_ok = bool(caddy_result.get("ok"))
        caddy_message = caddy_result.get("message") or "Routing sync failed."

        from .utils import log_event
        log_event(
            actor=getattr(request.user, 'username', None) or 'system',
            action='DOMAIN_ADD',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'domain': domain,
                'caddy_synced': caddy_ok,
            },
        )

        if not caddy_ok:
            logger.warning(
                "add_domain: domain saved but routing sync failed for %s (%s): %s",
                service.id,
                domain,
                caddy_message,
            )
            return Response(
                {
                    'domain': domain,
                    'domains': domains,
                    'cname_target': cname_target,
                    'server_ip': server_ip,
                    'message': (
                        f'{domain} was saved, but automatic routing sync failed. '
                        'Routing may not activate until Caddy reload succeeds.'
                    ),
                    'warning': caddy_message,
                    'caddy_synced': False,
                    'requires_redeploy': False,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return Response({
            'domain': domain,
            'domains': domains,
            'cname_target': cname_target,
            'server_ip': server_ip,
            'caddy_synced': caddy_ok,
            'routing_sync_deployment_id': None,
            'requires_redeploy': False,
            'dns_synced': False,
            'message': (
                f'{domain} added. Point DNS to the shown CNAME or server IP; '
                'SSL will be issued directly after verification. No redeploy required.'
            ),
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='delete-domain')
    def delete_domain(self, request, pk=None):
        """
        Remove a custom domain from the service.
        POST /api/v1/services/{id}/delete-domain/
        Body: { "domain": "myapp.com" }
        """
        service = self.get_object()
        assert_can_write(self.request.user, service)
        domain, domain_error = _normalize_request_domain(
            request.data.get('domain', '')
        )
        if domain_error:
            return Response(
                {'error': f'Invalid domain: {domain_error}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domains = [
            d for d in (service.custom_domains or [])
            if isinstance(d, str) and d.strip()
        ]
        if domain not in domains:
            return Response({'error': 'Domain not found'},
                            status=status.HTTP_404_NOT_FOUND)

        domains = [d for d in domains if d != domain]
        service.custom_domains = domains
        service.save(update_fields=['custom_domains'])

        from apps.domains.models import Domain
        Domain.objects.filter(domain_name=domain, service=service).delete()

        from apps.domains.models import Domain, DomainStatus
        # Clean up old domains
        Domain.objects.filter(service=service).exclude(domain_name__in=domains).delete()

        # Add new domains
        for d in domains:
            domain_obj, created = Domain.objects.get_or_create(
                domain_name=d,
                defaults={'service': service, 'status': DomainStatus.PENDING}
            )
            if created:
                from apps.domains.tasks import verify_dns_and_provision_ssl_task
                verify_dns_and_provision_ssl_task.delay(domain_obj.id)


        # Auto-sync Caddyfile so stale domain entry is removed immediately.
        caddy_result = self._sync_caddy()
        caddy_ok = bool(caddy_result.get("ok"))
        caddy_message = caddy_result.get("message") or "Routing sync failed."

        from .utils import log_event
        log_event(
            actor=getattr(request.user, 'username', None) or 'system',
            action='DOMAIN_DELETE',
            target=f'Service: {service.name}',
            metadata={
                'service_id': str(service.id),
                'domain': domain,
                'caddy_synced': caddy_ok,
            },
        )

        if not caddy_ok:
            logger.warning(
                "delete_domain: domain removed but routing sync failed for %s (%s): %s",
                service.id,
                domain,
                caddy_message,
            )
            return Response(
                {
                    'domain': domain,
                    'domains': domains,
                    'message': (
                        f'{domain} was removed, but automatic routing sync failed. '
                        'Old routing entries may persist until Caddy reload succeeds.'
                    ),
                    'warning': caddy_message,
                    'caddy_synced': False,
                    'requires_redeploy': False,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return Response({
            'domains': domains,
            'caddy_synced': caddy_ok,
            'routing_sync_deployment_id': None,
            'requires_redeploy': False,
            'message': f'{domain} removed. No redeploy required.',
        })

    def _resolve_target_type(self, service, latest_deploy):
        """Resolve execution target (remote/lite_agent/local) with fallback."""
        try:
            from apps.deployments.utils_target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target.get("server_obj")
            target_type = target.get("target_type")
        except Exception:
            active_server = self._resolve_remote_server(service, latest_deploy)
            target_type = "remote" if active_server else "local"
        return target_type, active_server

    def _dispatch_file_operation(self, service, latest_deploy, remote_config, local_action, path=None):
        """
        Dispatch a file operation to a remote node or local Docker container.

        Args:
            service: Service object.
            latest_deploy: Latest active deployment.
            remote_config: dict with:
                method (str), path_suffix (str),
                params (dict, optional), payload (dict, optional),
                timeout (int, optional, default 30),
                on_success (callable(resp)->Response, optional),
                on_error (callable(resp|None)->Response, optional),
                retry (callable(resp, orchestrator, remote_id, config)->Response|None, optional).
            local_action: callable(container, path=None) -> Response.
            path: Optional path string for symlink resolution.

        Returns:
            Response
        """
        target_type, active_server = self._resolve_target_type(service, latest_deploy)
        attempted_remote = target_type in ("remote", "lite_agent") and active_server

        if attempted_remote:
            from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
            orchestrator = RemoteOrchestrator(active_server)
            remote_id = orchestrator._search_remote_service(service, "/api/v1/services/")
            if not remote_id:
                return Response(
                    {'error': f'Service not found on remote node {active_server.name or active_server.host}'},
                    status=status.HTTP_404_NOT_FOUND,
                )
            try:
                resp = orchestrator._request(
                    method=remote_config['method'],
                    path=f"/api/v1/services/{remote_id}/{remote_config['path_suffix']}/",
                    params=remote_config.get('params'),
                    payload=remote_config.get('payload'),
                    timeout=remote_config.get('timeout', 30),
                )
                if resp and resp.status_code == 200:
                    on_success = remote_config.get('on_success')
                    if on_success:
                        return on_success(resp)
                    return Response(resp.json())
                retry_handler = remote_config.get('retry')
                if retry_handler:
                    retry_result = retry_handler(resp, orchestrator, remote_id, remote_config)
                    if retry_result is not None:
                        return retry_result
                on_error = remote_config.get('on_error')
                if on_error:
                    return on_error(resp)
                return Response(
                    {'error': f'Remote node {active_server.name or active_server.host} returned an error',
                     'details': resp.text[:500] if resp else 'Timeout'},
                    status=resp.status_code if resp else status.HTTP_502_BAD_GATEWAY,
                )
            except Exception as e:
                on_error = remote_config.get('on_error')
                if on_error:
                    return on_error(None)
                return Response(
                    {'error': f'Failed to reach {active_server.name or active_server.host}: {str(e)[:200]}'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        # Local execution (only reached when target is local)
        container = resolve_running_container(service, latest_deploy)
        if container is None:
            return Response({'error': 'No running container found'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Symlink resolution for Docker containers
        if path is not None:
            with contextlib.suppress(Exception):
                path = validate_and_sanitize_path(path, skip_system_check=True, container=container)

        return local_action(container, path) if path is not None else local_action(container)


    @action(detail=True, methods=['get'], url_path='file-browse')
    def file_browse(self, request, pk=None):
        """List files inside the running container (Docker, K8s, or remote node)."""
        service = self.get_object()
        path = request.query_params.get('path', '/')

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except Exception as e:
            logger.warning("file_browse 400: Path validation failed for %s: %s", path, str(e))
            return Response({
                'error': 'Path validation failed',
                'details': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            logger.warning("file_browse 400: No active deployment for %s", service.id)
            return Response({
                'error': 'No active deployment',
                'details': f'Deployment {service.id} has no active deployments'
            }, status=status.HTTP_400_BAD_REQUEST)

        def _retry_browse(resp, orchestrator, remote_id, config):
            """Retry file_browse with fallback paths."""
            # Stop retrying if the error indicates the node is down or unreachable
            if resp is None or resp.status_code >= 500:
                return resp

            original_path = config.get('params', {}).get('path', '')
            fallback_paths = ['/app', '/', '/var/www', '/opt', '/home']
            tried = {original_path}
            for fb in fallback_paths:
                if fb in tried:
                    continue
                tried.add(fb)
                logger.warning(
                    f"Remote file_browse failed for path {original_path}, "
                    f"trying fallback: {fb}."
                )
                try:
                    fb_resp = orchestrator._request(
                        method='GET',
                        path=f"/api/v1/services/{remote_id}/file-browse/",
                        params={'path': fb},
                        timeout=10,
                    )
                    if fb_resp and fb_resp.status_code == 200:
                        data = fb_resp.json()
                        data['path'] = fb
                        return Response(data)
                except Exception:
                    pass
            return None

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'GET',
                'path_suffix': 'file-browse',
                'params': {'path': path},
                'timeout': 30,
                'retry': _retry_browse,
            },
            local_action=lambda container, path=None: exec_file_list(container, path or '/', fallback_to_root=True),
            path=path,
        )

    def _k8s_file_browse(self, container_id: str, path: str):
        raise NotImplementedError("Kubernetes deployment is not supported. Use Docker or a lite agent.")

    def _k8s_exec_file_op(self, container_id: str, command_args: list):
        raise NotImplementedError("Kubernetes deployment is not supported. Use Docker or a lite agent.")

    def _resolve_remote_server(self, service, latest_deploy):
        """
        Fallback: resolve remote server when active_target_type is not set.
        Checks deployment's target_server, service.server, then provider.
        """
        from apps.deployments.models_core import ManagedServer
        # 1. Check deployment's target_server FK
        if latest_deploy and latest_deploy.target_server_id:
            target = latest_deploy.target_server
            if not target.is_primary:
                return target
        # 2. Check service.server FK
        server = getattr(service, 'server', None)
        if server and not server.is_primary:
            return server
        # 3. Check if service has a remote provider
        provider = getattr(service, 'provider', None)
        if provider and provider.provider_type in ('REMOTE', 'LITE_AGENT'):
            host = provider.host or getattr(provider, 'api_url', None)
            if host:
                return ManagedServer.objects.filter(
                    Q(host=host) | Q(private_ip=host)
                ).first()
        return None

    @action(detail=True, methods=['get'], url_path='file-download')
    def file_download(self, request, pk=None):
        """Download a file from the container."""
        service = self.get_object()
        path = request.query_params.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'GET',
                'path_suffix': 'file-download',
                'params': {'path': path},
                'timeout': 30,
                'on_success': lambda resp: StreamingHttpResponse(
                    resp.iter_content(chunk_size=8192),
                    content_type=resp.headers.get('Content-Type', 'application/x-tar'),
                ),
                'on_error': lambda resp: Response(
                    {'error': 'Failed to download from remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_download(container, path),
            path=path,
        )

    def _local_file_download(self, container, path: str):
        try:
            bits, _stat = container.get_archive(path)
            response = StreamingHttpResponse(bits, content_type='application/x-tar')
            filename = os.path.basename(path) + ".tar"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='file-delete')
    def file_delete(self, request, pk=None):
        """Delete a file or directory in the container."""
        service = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-delete',
                'payload': {'path': path},
                'timeout': 15,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to delete on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_delete(container, path),
            path=path,
        )

    def _local_file_delete(self, container, path: str):
        try:
            exit_code, output = container.exec_run(["rm", "-rf", path])
            if exit_code != 0:
                return Response({'error': 'Delete failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Deleted successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='file-mkdir')
    def file_mkdir(self, request, pk=None):
        """Create a directory in the container."""
        service = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-mkdir',
                'payload': {'path': path},
                'timeout': 15,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to mkdir on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_mkdir(container, path),
            path=path,
        )

    def _local_file_mkdir(self, container, path: str):
        try:
            exit_code, output = container.exec_run(["mkdir", "-p", path])
            if exit_code != 0:
                return Response({'error': 'Mkdir failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Created successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='file-read')
    def file_read(self, request, pk=None):
        """Read a file's contents from the running container."""
        service = self.get_object()
        path = request.query_params.get('path')

        if not path:
            return Response({'error': 'Path parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'GET',
                'path_suffix': 'file-read',
                'params': {'path': path},
                'timeout': 15,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to read file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_read(container, path),
            path=path,
        )

    def _local_file_read(self, container, path: str):
        try:
            exit_code, output = container.exec_run(["cat", path])
            if exit_code != 0:
                return Response({'error': 'Failed to read file', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)

            from django.conf import settings
            max_read_size = settings.SMSLY_MAX_FILE_READ_SIZE
            if len(output) > max_read_size:
                return Response({'error': 'File too large to read. Use download instead.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

            return Response({'path': path, 'content': output.decode('utf-8')})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='file-write')
    def file_write(self, request, pk=None):
        """Write contents to a file in the running container."""
        service = self.get_object()
        assert_can_write(self.request.user, service)
        path = request.data.get('path')
        content = request.data.get('content')

        if not path or content is None:
            return Response({'error': 'Path and content parameters are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-write',
                'payload': {'path': path, 'content': content},
                'timeout': 30,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to write file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_write(container, path, content),
            path=path,
        )

    def _local_file_write(self, container, path: str, content: str):
        try:
            import io
            import tarfile
            import time

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                file_data = content.encode('utf-8')
                tarinfo = tarfile.TarInfo(name=os.path.basename(path))
                tarinfo.size = len(file_data)
                tarinfo.mtime = int(time.time())
                tar.addfile(tarinfo, io.BytesIO(file_data))

            tar_stream.seek(0)
            dir_name = os.path.dirname(path)
            exit_code, output = container.exec_run(["mkdir", "-p", dir_name])
            if exit_code != 0:
                return Response({'error': 'Failed to create parent directory', 'details': output.decode()}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            success = container.put_archive(dir_name, tar_stream)

            if not success:
                return Response({'error': 'Failed to write file via put_archive'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            return Response({'message': 'File written successfully', 'path': path})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='file-upload')
    def file_upload(self, request, pk=None):
        """Upload a file to the running container."""
        import base64
        service = self.get_object()
        assert_can_write(self.request.user, service)
        path = request.data.get('path')

        if 'file' in request.FILES:
            uploaded_file = request.FILES['file']
            file_bytes = uploaded_file.read()
        elif 'content' in request.data:
            file_bytes = base64.b64decode(request.data['content'])
        else:
            return Response({'error': 'Path and file are required'}, status=status.HTTP_400_BAD_REQUEST)

        if not path:
            return Response({'error': 'Path is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resolved = posixpath.normpath(path)
        except Exception:
            return Response({'error': 'Invalid path'}, status=status.HTTP_400_BAD_REQUEST)

        if hasattr(service, 'volumes'):
            try:
                volumes = list(service.volumes.all())
            except Exception:
                volumes = []
            if volumes:
                mount_paths = [posixpath.normpath(v.mount_path).rstrip('/') or '/' for v in volumes]
                in_mount = False
                for mount in mount_paths:
                    if resolved == mount or resolved.startswith(mount + '/'):
                        try:
                            if posixpath.commonpath([resolved, mount]) == mount:
                                in_mount = True
                                break
                        except ValueError:
                            continue
                if not in_mount:
                    return Response({'error': 'Path traversal blocked'}, status=status.HTTP_400_BAD_REQUEST)
                path = resolved

        try:
            path = validate_and_sanitize_path(path, skip_system_check=True)
        except Exception as e:
            return Response({'error': 'Path validation failed', 'details': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        base_name = posixpath.basename(path)
        if not base_name or base_name.startswith('/') or '..' in base_name.split('/'):
            return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)

        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy:
            return Response({'error': 'No active deployment'}, status=status.HTTP_400_BAD_REQUEST)

        return self._dispatch_file_operation(
            service,
            latest_deploy,
            remote_config={
                'method': 'POST',
                'path_suffix': 'file-upload',
                'payload': {'path': path, 'content': base64.b64encode(file_bytes).decode('ascii')},
                'timeout': 60,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to upload file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
            },
            local_action=lambda container, path=None: self._local_file_upload(container, path, file_bytes),
            path=path,
        )

    def _local_file_upload(self, container, path: str, file_bytes: bytes):
        try:
            import io
            import tarfile
            import time

            base_name = posixpath.basename(path)
            if not base_name or base_name.startswith('/') or '..' in base_name.split('/'):
                return Response({'error': 'Invalid filename'}, status=status.HTTP_400_BAD_REQUEST)

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                tarinfo = tarfile.TarInfo(name=base_name)
                tarinfo.size = len(file_bytes)
                tarinfo.mtime = int(time.time())
                tar.addfile(tarinfo, io.BytesIO(file_bytes))

            tar_stream.seek(0)
            dir_name = posixpath.dirname(path)
            exit_code, output = container.exec_run(["mkdir", "-p", dir_name])
            if exit_code != 0:
                return Response({'error': 'Failed to create parent directory', 'details': output.decode()}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            success = container.put_archive(dir_name, tar_stream)

            if not success:
                return Response({'error': 'Failed to upload file via put_archive'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            return Response({'message': 'File uploaded successfully', 'path': path})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DeploymentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing Deployments.
    """
    queryset = Deployment.objects.all().order_by('-created_at')
    serializer_class = DeploymentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [
        parsers.JSONParser,
        parsers.MultiPartParser]  # Enable File Uploads
    # SECURITY (Batch H): same fix as ServiceViewSet. Throttles
    # are applied only to write methods (POST / PUT / PATCH /
    # DELETE) via get_throttles() below. Safe GETs (the
    # Activity Feed, Intelligence page, and per-deployment
    # polling) must not 429 the user.
    throttle_classes: list = []

    def get_throttles(self):
        """Apply the deployment-burst guard only to write methods.

        GET / HEAD / OPTIONS are safe. The deployment listing,
        activity feed, and logs views fire many GETs per page.
        """
        if self.request.method in permissions.SAFE_METHODS:
            return []
        return [BurstRateThrottle(), DeploymentRateThrottle()]
    def get_serializer_class(self):
        """
        Use lightweight serializer for list endpoints to avoid returning
        large log payloads for every deployment row.
        """
        if self.action == 'list':
            return DeploymentTimelineSerializer
        return DeploymentSerializer

    def get_queryset(self):
        """Return deployments for services accessible to the requesting user."""
        base_qs = self.queryset.select_related('service')
        if self.action == 'list':
            base_qs = base_qs.defer(
                'build_logs',
                'review_summary',
                'vulnerability_report',
                'pipeline_stages',
                'runtime_logs_url',
                'green_container_id',
                'container_id',
            )
        if self.request.user.is_superuser or is_remote_sync_request(self.request):
            return base_qs.all()

        project_id = self.request.query_params.get('project_id')
        if project_id:
            base_qs = base_qs.filter(service__project_id=project_id)

        return base_qs.filter(
            get_team_q_filter(self.request.user, prefix='service__', request=self.request)
        ).distinct()

    def _is_remote_sync_request(self):
        return is_remote_sync_request(self.request)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """
        Roll back (or forward) the service to this specific deployment.

        Triggers a new deployment using the commit hash / image artifact
        captured by ``target_deployment``. Only **successful** deployments
        (ACTIVE or INACTIVE) are allowed as rollback targets — rolling back
        to a FAILED or CANCELLED deployment would just re-run the broken
        code, which is never what the user wants.

        Refuses:
          - FAILED / CANCELLED deployments (must pick a successful release).
          - In-progress deployments (their commit/image may change while
            the build runs).
          - The deployment that is currently serving traffic (no-op — that
            would redeploy the same broken commit).
        """
        # Enforce explicit confirmation for rollback operations
        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return _error_response(
                "ROLLBACK_CONFIRMATION_REQUIRED",
                'Explicit confirmation required. Send "confirm": true.',
                user_action="Retry rollback with confirm=true.",
                retryable=True,
            )

        target_deployment = self.get_object()
        service = target_deployment.service
        guard = ServerGuard.check_user_workload_allowed(getattr(service, 'server', None))
        if not guard["ok"]:
            return Response(guard, status=status.HTTP_400_BAD_REQUEST)

        # Only successful deployments are valid rollback targets. Rolling back
        # to a FAILED or CANCELLED row would just re-trigger the broken code.
        successful = {
            Deployment.Status.ACTIVE,
            Deployment.Status.INACTIVE,
        }
        if target_deployment.status not in successful:
            return _error_response(
                "ROLLBACK_TARGET_NOT_SUCCESSFUL",
                (
                    f"Cannot rollback to a {target_deployment.status} deployment. "
                    "Only successful (ACTIVE / INACTIVE) deployments can be rolled back to."
                ),
                details={
                    "deployment_id": str(target_deployment.id),
                    "status": target_deployment.status,
                },
                user_action=(
                    "Pick a successful deployment from history (an ACTIVE or INACTIVE row), "
                    "or use /instant-rollback/ to auto-select the last good release."
                ),
            )

        # Validate the target deployment has a committed artifact to roll back to.
        if not target_deployment.commit_hash:
            return _error_response(
                "ROLLBACK_ARTIFACT_MISSING",
                "Cannot rollback: target deployment has no commit hash.",
                details={"deployment_id": str(target_deployment.id), "service_id": str(service.id)},
                user_action="Choose a deployment that has a valid commit hash/image artifact.",
            )

        # Reject in-progress deployments — their commit_hash / image may change
        # while the pipeline runs, so rolling back to "this row" is undefined.
        in_progress = {
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
            Deployment.Status.REVIEW,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
            Deployment.Status.AWAITING_APPROVAL,
        }
        if target_deployment.status in in_progress:
            return _error_response(
                "ROLLBACK_IN_PROGRESS",
                f"Cannot rollback to an in-progress ({target_deployment.status}) deployment.",
                details={
                    "deployment_id": str(target_deployment.id),
                    "status": target_deployment.status,
                },
                user_action=(
                    "Wait for the in-progress deployment to finish, or "
                    "cancel it, then retry rollback."
                ),
            )

        # Refuse to roll back to the deployment that is currently serving
        # traffic — that would redeploy the same commit/image and silently
        # no-op. Use Redeploy for force-rebuild of the current release, or
        # pick a PRIOR deployment from history.
        currently_active = (
            Deployment.objects
            .filter(service=service, status=Deployment.Status.ACTIVE)
            .order_by('-created_at')
            .first()
        )
        if currently_active and currently_active.id == target_deployment.id:
            return _error_response(
                "ROLLBACK_NOOP",
                "Cannot rollback to the deployment that is currently active — that would redeploy the same commit/image.",
                details={
                    "deployment_id": str(target_deployment.id),
                    "service_id": str(service.id),
                    "commit_hash": target_deployment.commit_hash,
                },
                user_action=(
                    "Pick a PRIOR deployment from history, or use /instant-rollback/ "
                    "to auto-select the last good release, or use Redeploy if you "
                    "just want to rebuild the current commit."
                ),
            )

        # Create new deployment record for the rollback
        new_deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash=target_deployment.commit_hash,
            commit_message=f"Rollback to {target_deployment.commit_hash[:7]}",
            branch=service.branch or '',
            is_rollback=True,
            rollback_from=target_deployment,
        )

        provider = _resolve_provider_for_service(service)
        if not provider:
            return _error_response(
                "ROLLBACK_PERMISSION_DENIED",
                "No active provider available.",
                details={"service_id": str(service.id)},
                user_action="Attach an active provider to this service, then retry rollback.",
            )
        smart_deploy_task.delay(deployment_id=str(new_deployment.id), provider_id=str(provider.id))
        payload = DeploymentSerializer(new_deployment).data
        payload["rollback_state"] = "rollback_pending"
        payload["rollback_target"] = str(target_deployment.id)

        AuditLog(
            actor=request.user.get_username(),
            action='DEPLOYMENT_ROLLBACK',
            target=f'Deployment: {new_deployment.id}',
            metadata={
                'service_id': str(service.id),
                'deployment_id': str(new_deployment.id),
                'target_deployment_id': str(target_deployment.id),
                'commit_hash': target_deployment.commit_hash,
            },
        ).save()

        return Response(payload, status=status.HTTP_201_CREATED)




    @action(detail=False, methods=['post'])
    def prune(self, request):
        """
        Global cleanup for failed deployments and orphaned containers.
        POST /api/v1/deployments/prune/

        1. Finds FAILED, ERROR, CANCELLED deployments for this user.
        2. Force-removes their containers on the VPS.
        3. Prunes dangling Docker images.
        4. Deletes the deployment records from DB.
        5. Cancels stuck QUEUED deployments (>1h old).

        SECURITY: the global ``client.images.prune(dangling=False)`` call
        removes every unused image on the host — reaping images other
        tenants' active services depend on. It is restricted to admins.
        Non-admins still get their own failed containers removed and
        dangling-image cleanup.
        """
        is_admin = bool(request.user and request.user.is_authenticated and request.user.is_staff)

        # ── 1. DB: Select deployments to prune ──
        base_qs = Deployment.objects.filter(
            status__in=['FAILED', 'ERROR', 'CANCELLED']
        )
        if not request.user.is_superuser:
            base_qs = base_qs.filter(service__owner=request.user)

        failed_deploys = list(base_qs.only('id', 'container_id'))

        # ── 1b. DB: Select failed addons to prune ──
        from apps.deployments.models_addons import Addon
        addon_qs = Addon.objects.filter(status='FAILED')
        if not request.user.is_superuser:
            addon_qs = addon_qs.filter(service__owner=request.user)
        failed_addons = list(addon_qs)

        # ── 2. VPS: Container cleanup ──
        containers_removed = 0
        images_pruned = 0
        try:
            # Increase timeout for global cleanup operations
            client = get_docker_client(timeout=60)
            # Remove specific failed containers
            if not failed_deploys and not failed_addons:
                logger.info("No failed deployments or addons found to prune from Docker.")

            for dep in failed_deploys:
                if dep.container_id:
                    try:
                        container = client.containers.get(dep.container_id)
                        container.remove(force=True)
                        containers_removed += 1
                    except Exception:
                        pass

            for addon_obj in failed_addons:
                container_name = f"smsly-addon-{addon_obj.addon_type.lower()}-{addon_obj.id}"
                try:
                    c = client.containers.get(container_name)
                    c.remove(force=True)
                    containers_removed += 1
                except Exception:
                    pass
                try:
                    c = client.containers.get(addon_obj.name)
                    c.remove(force=True)
                    containers_removed += 1
                except Exception:
                    pass

            # Prune all stopped containers to be sure
            client.containers.prune()

            # Prune unused images. SECURITY: the unfiltered
            # ``dangling: false`` prune affects every tenant on the
            # host. Restrict the global prune to admins; non-admins
            # only get their own dangling images (the safer default).
            if is_admin:
                image_prune_res = client.images.prune(filters={"dangling": ["false"]})
                images_pruned = image_prune_res.get("SpaceReclaimed", 0)
            else:
                image_prune_res = client.images.prune(filters={"dangling": ["true"]})
                images_pruned = image_prune_res.get("SpaceReclaimed", 0)

            # ── 2b. VPS: Temp backup cleanup ──
            # Clean up stale files in /tmp/backups (older than 1h)
            temp_backups_dir = '/tmp/backups'
            if os.path.exists(temp_backups_dir):
                import shutil
                import time
                now = time.time()
                for root, dirs, files in os.walk(temp_backups_dir):
                    for f in files:
                        f_path = os.path.join(root, f)
                        if os.stat(f_path).st_mtime < now - 3600:
                            with contextlib.suppress(OSError):
                                os.remove(f_path)
                    for d in dirs:
                        d_path = os.path.join(root, d)
                        if os.stat(d_path).st_mtime < now - 3600:
                            with contextlib.suppress(OSError):
                                shutil.rmtree(d_path)
        except Exception as exc:
            logger.warning("Docker/Temp prune failed during deployment cleanup: %s", exc)

        # ── 3. DB: Delete records ──
        count = base_qs.delete()[0]
        addon_qs.delete()[0]

        # ── 4. DB: Cancel stuck QUEUED deployments ──
        stale_threshold = timezone.now() - timezone.timedelta(minutes=30)
        stale_qs = Deployment.objects.filter(
            status='QUEUED',
            created_at__lt=stale_threshold
        )
        if not request.user.is_superuser:
            stale_qs = stale_qs.filter(service__owner=request.user)

        stale_count = stale_qs.update(
            status=Deployment.Status.CANCELLED,
            finished_at=timezone.now()
        )

        AuditLog(
            actor=request.user.get_username(),
            action='DEPLOYMENT_PRUNE',
            target='System',
            metadata={
                'deployments_deleted': count,
                'containers_removed': containers_removed,
                'stale_queued_cancelled': stale_count,
                'space_reclaimed_bytes': images_pruned,
            },
        ).save()

        return Response({
            'message': 'Cleanup complete',
            'deployments_deleted': count,
            'containers_removed': containers_removed,
            'stale_queued_cancelled': stale_count,
            'space_reclaimed_mb': round(images_pruned / (1024 * 1024), 2),
        })

    @action(detail=False, methods=['post'])
    def trigger(self, request):
        """
        Trigger a new deployment.
        POST /api/v1/deployments/trigger/
        Body: { "service_id": "uuid", "provider_id": "uuid" }

        Optional custom registry fields:
            registry_url, registry_username, registry_password

        If ``registry_url`` is provided, a new ephemeral Project is
        auto-created and the registry is scoped to that project. The
        service is moved to the new project and the registry override
        is stored on the Deployment for audit trail.
        """
        from django.utils import timezone

        from .models_project import Project
        from .models_registry_scope import ScopedRegistry

        serializer = DeploymentTriggerSerializer(data=request.data)
        if serializer.is_valid():
            service_id = serializer.validated_data['service_id']
            provider_id = serializer.validated_data['provider_id']
            if serializer.validated_data.get('skip_review', False):
                return Response(
                    {'error': 'skip_review is reserved for trusted internal deployment paths.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            skip_review = False

            try:
                # Verify service ownership before triggering deployment
                service = Service.objects.get(id=service_id, owner=request.user)

                guard = ServerGuard.check_user_workload_allowed(getattr(service, 'server', None))
                if not guard["ok"]:
                    return Response(guard, status=status.HTTP_400_BAD_REQUEST)
                provider = CloudProvider.objects.get(id=provider_id)

                # Prevent rapid-fire deployment spam
                existing = _has_active_deployment(service)
                if existing:
                    return Response({
                        'error': f'Deployment already in progress (status: {existing.status}). '
                                 'Wait for it to finish or cancel it first.',
                        'existing_deployment': DeploymentSerializer(existing).data,
                    }, status=status.HTTP_409_CONFLICT)

                # ── Custom registry → auto-create ephemeral project ──
                registry_override = None
                registry_url = serializer.validated_data.get('registry_url', '')

                if registry_url:
                    now_str = timezone.now().strftime('%Y%m%d-%H%M%S')
                    new_project = Project.objects.create(
                        owner=request.user,
                        name=f"Deploy-{service.name}-{now_str}",
                        description=f"Auto-created for custom registry deployment of {service.name}",
                        is_ephemeral=True,
                    )

                    # Create scoped registry for the new project
                    from django.contrib.contenttypes.models import ContentType
                    ct = ContentType.objects.get_for_model(Project)
                    ScopedRegistry.objects.create(
                        content_type=ct,
                        object_id=new_project.id,
                        registry_url=registry_url,
                        username=serializer.validated_data.get('registry_username', ''),
                        password=serializer.validated_data.get('registry_password', ''),
                    )

                    # Move service to the new project
                    old_project_id = str(service.project_id) if service.project_id else None
                    service.project = new_project
                    service.save(update_fields=['project', 'updated_at'])

                    # Store registry override on deployment for audit trail
                    registry_override = {
                        'url': registry_url,
                        'project_id': str(new_project.id),
                        'project_name': new_project.name,
                        'old_project_id': old_project_id,
                    }

                deployment = Deployment.objects.create(
                    service=service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=serializer.validated_data.get(
                        'commit_hash', 'latest'),
                    registry_override=registry_override,
                )

                smart_deploy_task.delay(
                    deployment_id=str(deployment.id),
                    provider_id=str(provider.id),
                    skip_review=skip_review
                )

                return Response({
                    'message': 'Deployment triggered successfully',
                    'deployment_id': deployment.id,
                    'status': deployment.status,
                }, status=status.HTTP_201_CREATED)

            except (Service.DoesNotExist, CloudProvider.DoesNotExist):
                return Response({'error': 'Resource not found'},
                                status=status.HTTP_404_NOT_FOUND)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancel a queued or building deployment.
        POST /api/v1/deployments/{id}/cancel/
        """
        deployment = self.get_object()

        if deployment.status not in (
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.AWAITING_APPROVAL,
        ):
            return Response(
                {'error': f'Cannot cancel deployment in {deployment.status} '
                          f'status. Only QUEUED, REVIEW, BUILDING, or AWAITING_APPROVAL '
                          f'deployments can be cancelled.'},
                status=status.HTTP_409_CONFLICT)

        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs += "\n\n[Cancelled] Deployment cancelled by user."

        # Clean up any running containers associated with this deployment
        try:
            if deployment.green_container_id or deployment.container_id:
                import docker
                client = docker.from_env()
                c_ids_to_remove = [id for id in [deployment.green_container_id, deployment.container_id] if id]
                cleaned_any = False
                for c_id in set(c_ids_to_remove):
                    try:
                        container = client.containers.get(c_id)
                        container.remove(force=True)
                        logger.info(f"Cleaned up cancelled container {c_id} for deployment {deployment.id}")
                        cleaned_any = True
                    except docker.errors.NotFound:
                        pass
                    except Exception as e:
                        logger.warning(f"Failed to cleanup container {c_id}: {e}")
                if cleaned_any:
                    deployment.build_logs += "\n🧹 Cleaned up container resources."
        except Exception as e:
            logger.warning(f"Docker client error during cancel cleanup: {e}")

        deployment.save()

        # Clean up orphaned build dir from analysis phase (REVIEW status only)
        if deployment.status == Deployment.Status.CANCELLED:
            import glob
            import shutil
            import tempfile
            tmp_pattern = os.path.join(
                tempfile.gettempdir(),
                f"build_{deployment.id}_*"
            )
            for d in glob.glob(tmp_pattern):
                shutil.rmtree(d, ignore_errors=True)

        return Response(DeploymentSerializer(deployment).data)

    @action(detail=False, methods=['post'], url_path='bulk-cancel')
    def bulk_cancel(self, request):
        """
        Cancel multiple deployments at once.
        POST /api/v1/deployments/bulk-cancel/
        Body: { "deployment_ids": ["uuid1", "uuid2", ...] }
        """
        deployment_ids = request.data.get('deployment_ids', [])
        if not deployment_ids or not isinstance(deployment_ids, list):
            return Response(
                {'error': 'deployment_ids must be a non-empty list'},
                status=status.HTTP_400_BAD_REQUEST)

        # Only allow cancelling deployments the user owns
        qs = self.get_queryset().filter(
            id__in=deployment_ids,
            status__in=[
                Deployment.Status.QUEUED,
                Deployment.Status.REVIEW,
                Deployment.Status.BUILDING,
                Deployment.Status.FAILED,
            ]
        )
        count = qs.update(
            status=Deployment.Status.CANCELLED,
            finished_at=timezone.now(),
        )

        if count:
            AuditLog(
                actor=request.user.get_username(),
                action='DEPLOYMENT_BULK_CANCEL',
                target='Deployment: multiple',
                metadata={
                    'count': count,
                    'deployment_ids': [str(d) for d in deployment_ids],
                },
            ).save()

        return Response({
            'cancelled': count,
            'message': f'{count} deployment(s) cancelled.',
        })

    @action(detail=True, methods=['get'])
    def review(self, request, pk=None):
        """
        Get pre-deploy review summary.
        GET /api/v1/deployments/{id}/review/
        Returns AI-recommended resources, env vars, issues, and addons.
        """
        deployment = self.get_object()

        if deployment.status != Deployment.Status.REVIEW:
            return Response(
                {'error': f'Deployment is in {deployment.status} status, '
                          'not awaiting review.'},
                status=status.HTTP_409_CONFLICT)

        return Response({
            'id': str(deployment.id),
            'service': str(deployment.service_id),
            'service_name': deployment.service.name,
            'status': deployment.status,
            'review_summary': deployment.review_summary,
            'build_logs': deployment.build_logs,
            'created_at': deployment.created_at.isoformat(),
        })

    @action(detail=True, methods=['post'], url_path='fill-external-env')
    def fill_external_env(self, request, pk=None):
        """
        Auto-fills unresolved external environment variables with safe placeholders.
        POST /api/v1/deployments/{id}/fill-external-env/
        """
        deployment = self.get_object()

        if deployment.status != Deployment.Status.REVIEW:
            return Response(
                {'error': f'Deployment is in {deployment.status} status, '
                          'not awaiting review.'},
                status=status.HTTP_409_CONFLICT)

        summary = deployment.review_summary or {}
        unresolved_vars = summary.get('unresolved_external_vars', [])

        if not unresolved_vars:
            return Response({'message': 'No unresolved external variables found.'})

        from apps.deployments.models import EnvironmentVariable
        from apps.deployments.services.manifest_env_resolver import ManifestEnvResolver

        injected = 0
        for var_name in unresolved_vars:
            key_upper = var_name.strip().upper()
            if not key_upper:
                continue

            # Check if user already set it
            if EnvironmentVariable.objects.filter(service=deployment.service, key=key_upper).exists():
                continue

            placeholder = ManifestEnvResolver.generate_placeholder_for_external(var_name)

            is_secret = any(hint in key_upper for hint in ["SECRET", "TOKEN", "PASSWORD", "PRIVATE_KEY", "API_KEY"])

            EnvironmentVariable.objects.create(
                service=deployment.service,
                key=key_upper,
                value=placeholder,
                is_secret=is_secret,
            )
            injected += 1

        # Clear the unresolved vars from the summary so it doesn't prompt again
        if 'unresolved_external_vars' in summary:
            del summary['unresolved_external_vars']
            deployment.review_summary = summary
            deployment.build_logs += f"\n✅ Auto-filled {injected} external variables with placeholders.\n"
            deployment.save(update_fields=['review_summary', 'build_logs'])

        return Response({
            'message': f'Auto-filled {injected} variables with placeholders.',
            'injected_count': injected,
        })

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Approve a deployment review and continue to build.
        POST /api/v1/deployments/{id}/approve/
        Body (all optional):
          { "cpu_cores": 1.0, "memory_mb": 1024,
            "env_overrides": {"KEY": "value"} }
        """
        deployment = self.get_object()

        if deployment.status != Deployment.Status.REVIEW:
            return Response(
                {'error': f'Deployment is in {deployment.status} status, '
                          'not awaiting approval.'},
                status=status.HTTP_409_CONFLICT)

        serializer = DeploymentApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        service = deployment.service
        updated_fields = []

        # Apply resource overrides
        cpu = data.get('cpu_cores')
        if cpu is not None:
            service.cpu_cores = cpu
            updated_fields.append('cpu_cores')

        mem = data.get('memory_mb')
        if mem is not None:
            service.memory_mb = mem
            updated_fields.append('memory_mb')

        if updated_fields:
            service.save(update_fields=updated_fields)

        # Apply env var overrides
        env_overrides = data.get('env_overrides', {})
        for key, value in env_overrides.items():
            key = key.strip().upper()
            if not key:
                continue
            EnvironmentVariable.objects.update_or_create(
                service=service, key=key,
                defaults={'value': value}
            )

        # Resolve provider BEFORE changing status (fail-safe: stays in
        # REVIEW if no provider, so user can retry)
        provider = _resolve_provider_for_target(
            service,
            target_is_local=bool(getattr(deployment, 'target_is_local', False)),
        )
        if not provider:
            message = (
                'No active local cloud provider configured'
                if getattr(deployment, 'target_is_local', False)
                else 'No active cloud provider configured'
            )
            return Response(
                {'error': message},
                status=status.HTTP_400_BAD_REQUEST)

        # Provider exists — now safe to transition status
        deployment.status = Deployment.Status.BUILDING
        deployment.started_at = timezone.now()
        deployment.save(update_fields=['status', 'started_at'])

        resume_deploy_task.delay(
            deployment_id=str(deployment.id), provider_id=str(provider.id)
        )

        return Response({
            'message': 'Deployment approved — build starting',
            'deployment': DeploymentSerializer(deployment).data,
        })

    @action(detail=True, methods=['get'], url_path='build-logs')
    def build_logs(self, request, pk=None):
        """
        Get build logs for a deployment (REST fallback for non-WebSocket).
        GET /api/v1/deployments/{id}/build-logs/
        """
        deployment = self.get_object()
        return Response({
            'id': str(deployment.id),
            'status': deployment.status,
            'build_logs': deployment.build_logs,
            'started_at': deployment.started_at,
            'finished_at': deployment.finished_at,
            'duration_seconds': deployment.duration_seconds,
        })


    @action(detail=True, methods=['get'], url_path='runtime-logs')
    def runtime_logs(self, request, pk=None):
        """
        Get live runtime logs from the deployed Docker container.
        GET /api/v1/deployments/{id}/runtime-logs/?tail=200
        """
        deployment = self.get_object()
        tail = int(request.query_params.get('tail', 200))
        tail = min(tail, 1000)  # Cap at 1000 lines

        service = deployment.service

        try:
            from apps.deployments.utils_target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target.get("server_obj")
            target_type = target.get("target_type")
        except Exception:
            active_server = getattr(service, 'server', None)
            target_type = "remote" if active_server and not active_server.is_primary else "local"

        if target_type in ("remote", "lite_agent") and active_server:
            if not deployment.remote_deployment_id:
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': 'No remote deployment ID found. The deployment may not have successfully synced to the remote node.',
                })
            try:
                from apps.deployments.services.remote_orchestrator import (
                    RemoteOrchestrator,
                )
                orchestrator = RemoteOrchestrator(active_server)
                resp = orchestrator._request(
                    method='GET',
                    path=f"/api/v1/deployments/{deployment.remote_deployment_id}/runtime-logs/",
                    params={'tail': tail},
                    timeout=15,
                )
                if resp and resp.status_code == 200:
                    data = resp.json()
                    # Re-map ID back to local deployment ID for frontend consistency
                    data['id'] = str(deployment.id)
                    return Response(data)

                err_detail = f"HTTP {resp.status_code if resp else 'None'}"
                if resp and resp.content:
                    try:
                        err_json = resp.json()
                        err_text = err_json.get("message") or err_json.get("detail") or str(err_json)
                        if err_text:
                            err_detail = f"HTTP {resp.status_code}: {err_text}"
                    except Exception:
                        pass
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': f"Failed to fetch logs from remote node: {err_detail}",
                })
            except Exception as e:
                logger.warning("Failed to proxy runtime logs to remote node: %s", e)
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': f"Remote proxy error: {e!s}",
                })

        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()

            # Find container by service name
            service_name = deployment.service.name
            containers = client.containers.list(
                filters={'name': service_name},
                limit=1,
            )

            if not containers:
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': 'No running container found for this service.',
                })

            container = containers[0]
            logs = container.logs(
                stdout=True,
                stderr=True,
                tail=tail,
                timestamps=True,
            )
            log_text = logs.decode('utf-8', errors='replace')

            return Response({
                'id': str(deployment.id),
                'container_id': container.short_id,
                'container_status': container.status,
                'runtime_logs': log_text,
            })

        except ImportError:
            return Response({
                'id': str(deployment.id),
                'runtime_logs': '',
                'message': 'Docker SDK not available.',
            })
        except Exception as e:
            logger.warning("Failed to fetch runtime logs for %s: %s", pk, e)
            err_msg = str(e)
            if any(term in err_msg.lower() for term in ["nameresolutionerror", "socket-proxy", "connection", "maxretryerror", "getaddrinfo"]):
                err_msg = "Cannot connect to Docker daemon or socket-proxy. Please verify Docker is running and reachable."
            return Response({
                'id': str(deployment.id),
                'runtime_logs': '',
                'message': f'Could not fetch runtime logs: {err_msg}',
            })

    @action(detail=True, methods=['post'])
    def diagnose(self, request, pk=None):
        """
        Trigger AI diagnosis for a deployment.
        """
        deployment = self.get_object()
        from apps.deployments.tasks_ai import analyze_failure_task

        # Trigger analysis asynchronously
        try:
            analyze_failure_task.delay(deployment_id=str(deployment.id))
        except Exception as exc:
            # Avoid hard-failing the API when the broker is unavailable.
            try:
                from kombu.exceptions import OperationalError as BrokerOperationalError
            except Exception:  # pragma: no cover
                BrokerOperationalError = ()

            if BrokerOperationalError and isinstance(exc, BrokerOperationalError):
                logger.warning(
                    "Unable to queue AI diagnosis task for deployment %s: broker unavailable",
                    deployment.id,
                )
            else:
                logger.exception(
                    "Unable to queue AI diagnosis task for deployment %s",
                    deployment.id,
                )

        return Response({'message': 'Analysis started'})

    @action(detail=False, methods=['post'], url_path='upload')
    def upload_source(self, request):
        """
        Upload source code (zip) for CLI deployment.
        """
        service_id = request.data.get('service_id')
        uploaded_file = request.FILES.get('file')

        if not service_id or not uploaded_file:
            return Response({'error': 'Missing service_id or file'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Security: File size limit (100MB)
        MAX_UPLOAD_SIZE = getattr(settings, 'MAX_UPLOAD_SIZE', 100 * 1024 * 1024)
        if uploaded_file.size > MAX_UPLOAD_SIZE:
            size_mb = uploaded_file.size / 1024 / 1024
            return Response(
                {'error': f'File too large. Maximum size is 100MB, '
                          f'got {size_mb:.1f}MB'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )

        # Security: Validate file extension
        if not uploaded_file.name.lower().endswith('.zip'):
            return Response(
                {'error': 'Invalid file type. Only .zip files are allowed'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # ZH-011 FIX: Verify ownership at query level (fail-closed)
            service = Service.objects.get(id=service_id, owner=request.user)

            # Security: Use secure upload directory
            import secrets
            base_dir = getattr(settings, 'MEDIA_ROOT', '/app/media')
            upload_dir = os.path.join(base_dir, 'uploads')
            os.makedirs(upload_dir, mode=0o700, exist_ok=True)

            # Generate unpredictable filename
            secure_name = f"{service_id}_{secrets.token_hex(16)}.zip"
            file_path = os.path.join(upload_dir, secure_name)

            with open(file_path, 'wb+') as destination:
                for chunk in uploaded_file.chunks():
                    destination.write(chunk)

            # Set restrictive file permissions
            os.chmod(file_path, 0o600)

            # Update Service to point to this file
            from pathlib import Path
            service.deploy_type = 'UPLOAD'
            service.repository_url = Path(file_path).resolve().as_uri()
            service.save(update_fields=['deploy_type', 'repository_url', 'updated_at'])

            # Trigger Deployment
            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash=f"upload-{uuid.uuid4().hex[:32]}",
                commit_message=f"CLI Upload: {uploaded_file.name}"
            )

            # If no provider set on service, find default
            provider = _resolve_provider_for_service(service)
            provider_id = str(provider.id) if provider else None
            if not provider_id:
                return Response(
                    {'error': 'No active cloud provider configured'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id)

            return Response({
                'message': 'Source uploaded and deployment triggered',
                'deployment_id': deployment.id,
                'file_size': uploaded_file.size
            }, status=status.HTTP_201_CREATED)

        except Service.DoesNotExist:
            return Response({'error': 'Service not found'},
                            status=status.HTTP_404_NOT_FOUND)


from .views_audit import (
    AuditLogViewSet,  # noqa: F401  (re-export for backwards compat — see views_audit.py)
)
from .views_auth import (
    SessionTokenView,  # noqa: F401  (re-export for backwards compat — see views_auth.py)
)


class SystemConfigView(GenericAPIView):
    """
    Expose safe server configuration to the frontend.
    GET /api/v1/system/config/
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def _maintenance_task_response(self, task_id: str):
        task_id = str(task_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", task_id):
            return Response(
                {"error": "Invalid maintenance task id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = AsyncResult(task_id)
        payload = {
            "task_id": task_id,
            "state": result.state,
            "status": "running",
            "message": "Maintenance task is still running.",
        }

        if result.state == "PENDING":
            payload["status"] = "queued"
            payload["message"] = "Maintenance task is queued or waiting for a worker."
        elif result.state == "STARTED":
            payload["status"] = "running"
        elif result.state == "SUCCESS":
            task_result = result.result or {}
            if isinstance(task_result, dict):
                payload.update({
                    "status": task_result.get("status", "success"),
                    "message": task_result.get("message", "Maintenance task completed."),
                    "result": task_result,
                })
            else:
                payload.update({
                    "status": "success",
                    "message": "Maintenance task completed.",
                    "result": task_result,
                })
        elif result.state == "FAILURE":
            payload.update({
                "status": "error",
                "message": str(result.result or "Maintenance task failed."),
            })
        elif isinstance(result.info, dict):
            payload.update({
                "status": result.info.get("status", payload["status"]),
                "message": result.info.get("message", payload["message"]),
                "meta": result.info,
            })

        return Response(payload)

    def get(self, request):
        task_id = (
            request.query_params.get("maintenance_task_id")
            or request.query_params.get("task_id")
        )
        if task_id:
            return self._maintenance_task_response(task_id)

        infra_health = self._get_infra_health()

        safe_data = {
            'VERSION': '3.0.0',
            'DOMAIN': getattr(settings, 'DOMAIN', 'localhost'),
            'safe_update_available': os.path.exists('/opt/smsly-hosting/scripts/safe-update.sh'),
            'MAPBOX_TOKEN': PlatformConfig.get_config_value('mapbox_token'),
            **self._get_storage_metrics(),
            **infra_health,
        }

        if not request.user.is_superuser:
            return Response(safe_data)

        return Response({
            **safe_data,
            # General
            'DEBUG': settings.DEBUG,
            'TIME_ZONE': settings.TIME_ZONE,
            'SITE_ID': settings.SITE_ID,

            # Security
            'USE_SSL': getattr(settings, 'SECURE_SSL_REDIRECT', False),
            'SECURE_SSL_REDIRECT': getattr(settings, 'SECURE_SSL_REDIRECT', False),
            'SECURE_HSTS_SECONDS': getattr(settings, 'SECURE_HSTS_SECONDS', 0),
            'SECURE_HSTS_INCLUDE_SUBDOMAINS': getattr(settings, 'SECURE_HSTS_INCLUDE_SUBDOMAINS', False),
            'SECURE_HSTS_PRELOAD': getattr(settings, 'SECURE_HSTS_PRELOAD', False),
            'SESSION_COOKIE_SECURE': getattr(settings, 'SESSION_COOKIE_SECURE', False),
            'CSRF_COOKIE_SECURE': getattr(settings, 'CSRF_COOKIE_SECURE', False),
            'SMSLY_DISABLE_SIGNATURE_CHECK': getattr(settings, 'SMSLY_DISABLE_SIGNATURE_CHECK', False),

            # Network
            'ALLOWED_HOSTS': settings.ALLOWED_HOSTS,
            'CORS_ALLOWED_ORIGINS': getattr(settings, 'CORS_ALLOWED_ORIGINS', []),
            'CSRF_TRUSTED_ORIGINS': getattr(settings, 'CSRF_TRUSTED_ORIGINS', []),

            # Auth
            'ACCOUNT_AUTH_METHOD': getattr(settings, 'ACCOUNT_AUTHENTICATION_METHOD', 'username'),
            'LOGIN_REDIRECT_URL': getattr(settings, 'LOGIN_REDIRECT_URL', '/'),

            # Infrastructure — Redis / Celery
            'REDIS_HOST': getattr(settings, 'REDIS_HOST', 'redis'),
            'REDIS_PORT': getattr(settings, 'REDIS_PORT', '6379'),
            'REDIS_PASSWORD_SET': bool(getattr(settings, 'REDIS_PASSWORD', '')),
            'CELERY_RESULT_BACKEND': getattr(settings, 'CELERY_RESULT_BACKEND', ''),

            # Container Registry
            'CONTAINER_REGISTRY_URL': getattr(settings, 'CONTAINER_REGISTRY_URL', ''),
            'REGISTRY_USER': getattr(settings, 'REGISTRY_USER', '') or 'Not set',
            'REGISTRY_PASSWORD_SET': bool(getattr(settings, 'REGISTRY_PASSWORD', '')),

            # Rate Limiting
            'THROTTLE_RATES': settings.REST_FRAMEWORK.get('DEFAULT_THROTTLE_RATES', {}),

            # Database (H-2 fix: expose only safe boolean flags, not internals)
            'DATABASE_CONFIGURED': bool(settings.DATABASES['default'].get('HOST')),
            'DATABASE_ENGINE_TYPE': 'postgres' if 'postgresql' in settings.DATABASES['default'].get('ENGINE', '') else 'other',

            # Webhook
            'GITHUB_WEBHOOK_SECRET_SET': bool(getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')),
            'GITLAB_WEBHOOK_SECRET_SET': bool(getattr(settings, 'GITLAB_WEBHOOK_SECRET', '')),
            'BITBUCKET_WEBHOOK_SECRET_SET': bool(getattr(settings, 'BITBUCKET_WEBHOOK_SECRET', '')),

            # Maintenance actions available to admins (labels only, no flags)
            'maintenance_actions': [
                {'action': key, 'label': spec['label']}
                for key, spec in MAINTENANCE_ACTIONS.items()
            ],
        })

    def _get_storage_metrics(self):
        """Fetch server root partition storage metrics using psutil or shutil."""
        import shutil
        try:
            total, used, free = shutil.disk_usage("/")
            return {
                'STORAGE_TOTAL_GB': round(total / (2**30), 2),
                'STORAGE_USED_GB': round(used / (2**30), 2),
                'STORAGE_FREE_GB': round(free / (2**30), 2),
                'STORAGE_USED_PERCENT': round((used / total) * 100, 1) if total > 0 else 0,
            }
        except Exception:
            return {
                'STORAGE_TOTAL_GB': 0,
                'STORAGE_USED_GB': 0,
                'STORAGE_FREE_GB': 0,
                'STORAGE_USED_PERCENT': 0,
            }

    def _get_infra_health(self):
        """Check live infrastructure health: host metrics + all PaaS services."""
        import subprocess
        import time

        infra = {
            'cpu_percent': 0.0,
            'ram_total_mb': 0,
            'ram_used_mb': 0,
            'ram_percent': 0.0,
            'load_avg': [0.0, 0.0, 0.0],
            'disk_total_gb': 0.0,
            'disk_used_gb': 0.0,
            'disk_percent': 0.0,
            'uptime_seconds': 0,
            'services': {},
        }

        # ── Host metrics ──────────────────────────────────────────
        try:
            import psutil
            infra['cpu_percent'] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            infra['ram_total_mb'] = round(mem.total / (1024 * 1024))
            infra['ram_used_mb'] = round(mem.used / (1024 * 1024))
            infra['ram_percent'] = mem.percent
            load = psutil.getloadavg()
            infra['load_avg'] = [round(x, 2) for x in load]
            disk = psutil.disk_usage('/')
            infra['disk_total_gb'] = round(disk.total / (2**30), 2)
            infra['disk_used_gb'] = round(disk.used / (2**30), 2)
            infra['disk_percent'] = round(disk.percent, 1)
            infra['uptime_seconds'] = int(time.time() - psutil.boot_time())
        except ImportError:
            try:
                with open('/proc/loadavg') as f:
                    parts = f.read().split()
                    infra['load_avg'] = [float(parts[i]) for i in range(3)]
            except Exception:
                pass

        # ── Docker containers ─────────────────────────────────────
        KNOWN_SERVICES = [
            # Core
            'backend', 'frontend', 'celery', 'celery-beat', 'celery-fast', 'celery-deploy',
            # Database
            'db', 'db-replica', 'postgres-primary', 'postgres-replica', 'pgcat',
            'pgbouncer', 'pgbouncer-readonly',
            # Cache / Redis HA
            'redis', 'redis-primary', 'redis-replica',
            'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3',
            # Queue
            'rabbitmq',
            # Proxy / Routing
            'traefik', 'caddy', 'route-fallback', 'socket-proxy', 'frps',
            # Observability
            'grafana', 'loki', 'promtail', 'prometheus', 'alertmanager',
            'cadvisor', 'node-exporter',
            # Security
            'crowdsec', 'smsly-falco', 'infisical',
            # Registry / Build
            'registry', 'docker-mirror', 'verdaccio', 'buildkitd',
            # Misc
            'apt-cacher', 'docker-labels',
        ]

        import re

        def _match_container_name(container_name, svc_name):
            """Check if container_name corresponds to svc_name.
            Docker Compose names containers as project-service-N
            (e.g. smsly-hosting-backend-1, smsly-postgres-primary, smsly-crowdsec).
            Match when svc_name appears as a hyphen-delimited suffix component."""
            return bool(re.search(rf'(?:-|^){re.escape(svc_name)}(?:-\d+)?$', container_name))

        running_map = {}  # name -> status string
        try:
            result = subprocess.run(
                ['docker', 'ps', '-a', '--format', '{{.Names}}\t{{.Status}}\t{{.State}}'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        name, status, state = parts[0].strip(), parts[1].strip(), parts[2].strip()
                        running_map[name] = {'status': status, 'running': state == 'running'}
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        def _find_container(svc_name):
            """Look up a service in running_map by matching container naming conventions."""
            for container_name, info in running_map.items():
                if _match_container_name(container_name, svc_name):
                    return info
            return None

        # ── Service health probes ─────────────────────────────────
        # Database
        db_ok = False
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                db_ok = True
        except Exception:
            pass

        # Redis
        redis_ok = False
        try:
            import redis as redis_lib
            r = redis_lib.Redis(
                host=getattr(settings, 'REDIS_HOST', 'redis'),
                port=int(getattr(settings, 'REDIS_PORT', 6379)),
                password=getattr(settings, 'REDIS_PASSWORD', '') or None,
                socket_timeout=2,
            )
            r.ping()
            redis_ok = True
        except Exception:
            pass

        # Celery workers
        celery_ok = False
        try:
            from celery import app as celery_app
            inspect = celery_app.control.inspect(timeout=2)
            active = inspect.active()
            if active is not None:
                celery_ok = True
        except Exception:
            pass

        # HTTP health probes for services with /health or /ping endpoints
        HTTP_PROBES = {
            'grafana': 'http://localhost:3001/api/health',
            'prometheus': 'http://localhost:9090/-/healthy',
            'loki': 'http://localhost:3100/ready',
            'rabbitmq': 'http://localhost:15672/api/health/checks/alarms',
            'pgcat': 'http://localhost:6432/pool',
            'alertmanager': 'http://localhost:9093/-/healthy',
        }

        def _http_probe(url: str) -> bool:
            try:
                import urllib.request
                req = urllib.request.Request(url, method='GET')
                resp = urllib.request.urlopen(req, timeout=2)
                return resp.status < 400
            except Exception:
                return False

        from concurrent.futures import ThreadPoolExecutor, as_completed
        http_results = {}
        with ThreadPoolExecutor(max_workers=len(HTTP_PROBES)) as pool:
            futures = {pool.submit(_http_probe, url): svc for svc, url in HTTP_PROBES.items()}
            for future in as_completed(futures):
                http_results[futures[future]] = future.result()

        # ── Build final service map ───────────────────────────────
        for svc_name in KNOWN_SERVICES:
            container = _find_container(svc_name)
            if container:
                running = container['running']
            elif svc_name in ('db',):
                running = db_ok
            elif svc_name in ('redis', 'redis-primary'):
                running = redis_ok
            elif svc_name in ('celery', 'celery-beat', 'celery-fast', 'celery-deploy'):
                running = celery_ok
            elif svc_name in http_results:
                running = http_results[svc_name]
            else:
                running = False

            # Override for HA replicas / sentinels — use docker state
            if svc_name in ('postgres-replica', 'redis-replica',
                            'redis-sentinel-1', 'redis-sentinel-2', 'redis-sentinel-3'):
                if container:
                    running = container['running']

            infra['services'][svc_name] = {
                'running': running,
                'status': container['status'] if container else ('healthy' if running else 'missing'),
            }

        # ── Host-level security ───────────────────────────────────
        host_security = {}

        # UFW
        try:
            result = subprocess.run(
                ['ufw', 'status'], capture_output=True, text=True, timeout=3,
            )
            host_security['ufw'] = {
                'installed': result.returncode == 0 or 'not found' not in (result.stderr or '').lower(),
                'active': 'active' in (result.stdout or '').lower(),
            }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            host_security['ufw'] = {'installed': False, 'active': False}

        # fail2ban
        try:
            result = subprocess.run(
                ['fail2ban-client', 'ping'], capture_output=True, text=True, timeout=3,
            )
            host_security['fail2ban'] = {
                'installed': True,
                'active': 'pong' in (result.stdout or '').lower(),
            }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            host_security['fail2ban'] = {'installed': False, 'active': False}

        # auditd
        try:
            result = subprocess.run(
                ['systemctl', 'is-active', 'auditd'],
                capture_output=True, text=True, timeout=3,
            )
            host_security['auditd'] = {
                'installed': result.returncode == 0 or 'could not be found' not in (result.stderr or '').lower(),
                'active': (result.stdout or '').strip() == 'active',
            }
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            host_security['auditd'] = {'installed': False, 'active': False}

        infra['host_security'] = host_security

        return infra

    def post(self, request):
        """Queue a maintenance task via the API."""
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return Response(
                {"error": "Admin privileges are required for maintenance actions."},
                status=status.HTTP_403_FORBIDDEN,
            )
        action = str(request.data.get('action') or '').strip().lower()
        action_spec = MAINTENANCE_ACTIONS.get(action)
        if not action_spec:
            return Response(
                {"error": "Invalid maintenance action specified. Use clear, update, refresh, registry_gc, or build_cache."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .tasks import run_maintenance_task

        lock_key = f"smsly:maintenance:{action}:lock"
        task_id = str(uuid.uuid4())
        if not cache.add(lock_key, task_id, timeout=action_spec["lock_ttl"]):
            existing_task_id = cache.get(lock_key)
            return Response(
                {
                    "status": "running",
                    "action": action,
                    "task_id": existing_task_id,
                    "message": f"{action_spec['label']} is already running.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            task = run_maintenance_task.apply_async(
                kwargs={
                    "command_flag": action_spec["flag"],
                    "lock_key": lock_key,
                },
                task_id=task_id,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            cache.delete(lock_key)
            logger.exception("Failed to queue maintenance action %s: %s", action, exc)
            return Response(
                {
                    "status": "error",
                    "action": action,
                    "message": "Failed to queue maintenance task. Check Celery/RabbitMQ availability.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = {
            "status": "queued",
            "action": action,
            "task_id": task.id or task_id,
            "message": action_spec["queued_message"],
        }

        if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False) and task.ready():
            cache.delete(lock_key)
            task_result = task.result or {}
            if isinstance(task_result, dict):
                payload.update({
                    "status": task_result.get("status", "success"),
                    "message": task_result.get("message", payload["message"]),
                    "result": task_result,
                })
                status_code = (
                    status.HTTP_200_OK
                    if task_result.get("status") == "success"
                    else status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            else:
                payload.update({"status": "success", "result": task_result})
                status_code = status.HTTP_200_OK
            return Response(payload, status=status_code)

        return Response(payload, status=status.HTTP_202_ACCEPTED)


class DomainConfigView(GenericAPIView):
    """
    Manage platform domain & SSL configuration.
    GET  /api/v1/system/domain-config/ → current config
    PUT  /api/v1/system/domain-config/ → update + apply Caddyfile
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        config = PlatformConfig.load()
        return Response({
            'domain': config.domain,
            'use_ssl': config.use_ssl,
            'wildcard_subdomains': config.wildcard_subdomains,
            'cloudflare_api_token_set': bool(config.cloudflare_api_token),
            'github_webhook_secret_set': bool(config.github_webhook_secret) or bool(os.environ.get('GITHUB_WEBHOOK_SECRET', '')),
            'gitlab_webhook_secret_set': bool(config.gitlab_webhook_secret) or bool(os.environ.get('GITLAB_WEBHOOK_SECRET', '')),
            'bitbucket_webhook_secret_set': bool(config.bitbucket_webhook_secret) or bool(os.environ.get('BITBUCKET_WEBHOOK_SECRET', '')),
            'server_ip': config.server_ip or '',
            'caddy_status': config.caddy_status,
            'max_concurrent_builds': config.max_concurrent_builds,
            'ecosystem_max_concurrent_builds': config.ecosystem_max_concurrent_builds,
            'ecosystem_build_stagger_seconds': config.ecosystem_build_stagger_seconds,
            'ecosystem_default_wave_size': config.ecosystem_default_wave_size,
            'ecosystem_wave_recheck_seconds': config.ecosystem_wave_recheck_seconds,
            # Billing
            'billing_currency': config.billing_currency,
            'billing_pro_amount': config.billing_pro_amount,
            'billing_pro_period_days': config.billing_pro_period_days,
            # SMSLY Platform
            'smsly_sms_api_url': config.smsly_sms_api_url,
            'smsly_voice_api_url': config.smsly_voice_api_url,
            'smsly_platform_api_url': config.smsly_platform_api_url,
            'smsly_internal_api_key_set': bool(config.smsly_internal_api_key),
            # Alerting
            'alert_phone_number': config.alert_phone_number,
            'critical_alert_phone': config.critical_alert_phone,
            'notify_on_success': config.notify_on_success,
            # Container Registry
            'container_registry_url': config.container_registry_url,
            'registry_user': config.registry_user,
            'registry_password_set': bool(config.registry_password),
            # Observability
            'sentry_dsn_set': bool(config.sentry_dsn),
            'sentry_traces_sample_rate': config.sentry_traces_sample_rate,
            'sentry_profiles_sample_rate': config.sentry_profiles_sample_rate,
            'sentry_environment': config.sentry_environment,
            # Feature Flags
            'smsly_disable_tier_gates': config.smsly_disable_tier_gates,
            'enable_legacy_tunnel_api': config.enable_legacy_tunnel_api,
            'smsly_strict_ssh_host_key_check': config.smsly_strict_ssh_host_key_check,
            'enable_crowdsec_waf': config.enable_crowdsec_waf,
            'trivy_enabled': config.trivy_enabled,
            'trivy_fail_on_severity': config.trivy_fail_on_severity,
            'cosign_enabled': config.cosign_enabled,
            'cosign_require_verification': config.cosign_require_verification,
            'backup_require_encryption': config.backup_require_encryption,
            'enforce_device_trust': config.enforce_device_trust,
            # Traffic Geo
            'traffic_geo_enabled': config.traffic_geo_enabled,
            'mapbox_token_set': bool(config.mapbox_token),
            # CrowdSec
            'crowdsec_bouncer_key_set': bool(config.crowdsec_bouncer_key),
            'crowdsec_enroll_key_set': bool(config.crowdsec_enroll_key),
            # SMTP
            'smtp_host': config.smtp_host,
            'smtp_port': config.smtp_port,
            'smtp_username': config.smtp_username,
            'smtp_password_set': bool(config.smtp_password),
            'smtp_use_tls': config.smtp_use_tls,
            'smtp_from_email': config.smtp_from_email,
            'smtp_from_name': config.smtp_from_name,
            'updated_at': config.updated_at,
        })

    @staticmethod
    def _rewrite_service_public_domains(old_base_domain: str, new_base_domain: str) -> int:
        """
        Rewrite generated service public domains onto the new platform base domain.

        Only domains currently using the previous platform base are rewritten.
        Custom domains stay untouched.
        """
        updated = 0
        host_keys = ("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS")
        for service in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain="").iterator():
            current_domain = str(service.public_domain or "").strip().lower().rstrip(".")
            next_domain = _rewrite_public_domain(current_domain, old_base_domain, new_base_domain)
            if not next_domain or next_domain == current_domain:
                continue

            if Service.objects.exclude(pk=service.pk).filter(public_domain=next_domain).exists():
                logger.warning(
                    "Skipping public domain rewrite for service=%s due to conflict on %s",
                    service.id,
                    next_domain,
                )
                continue

            service.public_domain = next_domain
            service.save(update_fields=["public_domain"])

            EnvironmentVariable.objects.filter(
                service=service,
                key="PUBLIC_DOMAIN",
            ).update(value=next_domain)

            for env_var in EnvironmentVariable.objects.filter(service=service, key__in=host_keys):
                value = str(env_var.value or "")
                if current_domain in value and next_domain not in value:
                    env_var.value = value.replace(current_domain, next_domain)
                    env_var.save(update_fields=["value"])

            updated += 1

        return updated

    def put(self, request):
        # SECURITY (Issue 22): wrap the whole update under a row
        # lock on the PlatformConfig singleton so two concurrent
        # admins cannot race the Caddyfile/DNS apply. The
        # Caddyfile is applied first; if the subsequent DNS apply
        # raises, the transaction rolls back the DB writes
        # (caddy_status, updated_at) and the caller sees a 5xx
        # with no partial DB state.
        with transaction.atomic():
            config = PlatformConfig.objects.select_for_update().get(pk=1)
            data = request.data
            previous_base_domain = Service.default_public_base_domain()
            original_domain = (config.domain or "").strip().lower().rstrip(".")

            # Update fields
            if 'domain' in data:
                raw_domain = str(data.get('domain') or '').strip()
                if raw_domain:
                    domain, domain_error = _normalize_request_domain(raw_domain)
                    if domain_error:
                        return Response(
                            {'error': f'Invalid domain: {domain_error}'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    config.domain = domain
                else:
                    config.domain = ''
            if 'use_ssl' in data:
                config.use_ssl = _parse_bool(data.get('use_ssl'))
            if 'wildcard_subdomains' in data:
                config.wildcard_subdomains = _parse_bool(data.get('wildcard_subdomains'))
            if 'enable_crowdsec_waf' in data:
                config.enable_crowdsec_waf = _parse_bool(data.get('enable_crowdsec_waf'))
            if 'cloudflare_api_token' in data:
                # Allow explicit clear by sending an empty string.
                config.cloudflare_api_token = str(
                    data.get('cloudflare_api_token') or ''
                ).strip()
            clearing_token = 'cloudflare_api_token' in data and not config.cloudflare_api_token
            for _secret_field in ('github_webhook_secret', 'gitlab_webhook_secret', 'bitbucket_webhook_secret'):
                if _secret_field in data:
                    val = str(data.get(_secret_field) or '').strip()
                    setattr(config, _secret_field, val)
            if 'server_ip' in data:
                config.server_ip = str(data.get('server_ip') or '').strip() or None
            if 'max_concurrent_builds' in data:
                try:
                    config.max_concurrent_builds = max(1, min(10, int(data['max_concurrent_builds'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_max_concurrent_builds' in data:
                try:
                    config.ecosystem_max_concurrent_builds = max(1, min(10, int(data['ecosystem_max_concurrent_builds'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_build_stagger_seconds' in data:
                try:
                    config.ecosystem_build_stagger_seconds = max(0, min(300, int(data['ecosystem_build_stagger_seconds'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_default_wave_size' in data:
                try:
                    config.ecosystem_default_wave_size = max(1, min(5, int(data['ecosystem_default_wave_size'])))
                except (TypeError, ValueError):
                    pass
            if 'ecosystem_wave_recheck_seconds' in data:
                try:
                    config.ecosystem_wave_recheck_seconds = max(5, min(300, int(data['ecosystem_wave_recheck_seconds'])))
                except (TypeError, ValueError):
                    pass
            # Billing
            if 'billing_currency' in data:
                config.billing_currency = str(data.get('billing_currency') or 'USD').strip()[:10]
            if 'billing_pro_amount' in data:
                config.billing_pro_amount = str(data.get('billing_pro_amount') or '29.00').strip()[:20]
            if 'billing_pro_period_days' in data:
                try:
                    config.billing_pro_period_days = max(1, int(data['billing_pro_period_days']))
                except (TypeError, ValueError):
                    pass
            # SMSLY Platform
            for _field in ('smsly_sms_api_url', 'smsly_voice_api_url', 'smsly_platform_api_url'):
                if _field in data:
                    setattr(config, _field, str(data.get(_field) or '').strip()[:300])
            if 'smsly_internal_api_key' in data:
                config.smsly_internal_api_key = str(data.get('smsly_internal_api_key') or '').strip()
            # Alerting
            for _field in ('alert_phone_number', 'critical_alert_phone'):
                if _field in data:
                    setattr(config, _field, str(data.get(_field) or '').strip()[:20])
            if 'notify_on_success' in data:
                config.notify_on_success = _parse_bool(data.get('notify_on_success'))
            # Container Registry
            if 'container_registry_url' in data:
                config.container_registry_url = str(data.get('container_registry_url') or '').strip()[:255]
            if 'registry_user' in data:
                config.registry_user = str(data.get('registry_user') or '').strip()[:255]
            if 'registry_password' in data:
                config.registry_password = str(data.get('registry_password') or '').strip()
            # Observability
            if 'sentry_dsn' in data:
                config.sentry_dsn = str(data.get('sentry_dsn') or '').strip()[:300]
            if 'sentry_traces_sample_rate' in data:
                try:
                    config.sentry_traces_sample_rate = max(0.0, min(1.0, float(data['sentry_traces_sample_rate'])))
                except (TypeError, ValueError):
                    pass
            if 'sentry_profiles_sample_rate' in data:
                try:
                    config.sentry_profiles_sample_rate = max(0.0, min(1.0, float(data['sentry_profiles_sample_rate'])))
                except (TypeError, ValueError):
                    pass
            if 'sentry_environment' in data:
                config.sentry_environment = str(data.get('sentry_environment') or 'production').strip()[:50]
            # Traffic Geo
            if 'traffic_geo_enabled' in data:
                config.traffic_geo_enabled = _parse_bool(data.get('traffic_geo_enabled'))
            if 'mapbox_token' in data:
                config.mapbox_token = str(data.get('mapbox_token') or '').strip()
            # CrowdSec
            if 'crowdsec_bouncer_key' in data:
                config.crowdsec_bouncer_key = str(data.get('crowdsec_bouncer_key') or '').strip()
            if 'crowdsec_enroll_key' in data:
                config.crowdsec_enroll_key = str(data.get('crowdsec_enroll_key') or '').strip()
            # Feature Flags
            for _field in ('smsly_disable_tier_gates', 'enable_legacy_tunnel_api', 'smsly_strict_ssh_host_key_check'):
                if _field in data:
                    setattr(config, _field, _parse_bool(data.get(_field)))
            # Security Scanning
            if 'trivy_enabled' in data:
                config.trivy_enabled = _parse_bool(data.get('trivy_enabled'))
            if 'trivy_fail_on_severity' in data:
                val = str(data.get('trivy_fail_on_severity') or 'CRITICAL').strip().upper()
                if val in ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'):
                    config.trivy_fail_on_severity = val
            # Cosign Image Signing
            if 'cosign_enabled' in data:
                config.cosign_enabled = _parse_bool(data.get('cosign_enabled'))
            if 'cosign_require_verification' in data:
                config.cosign_require_verification = _parse_bool(data.get('cosign_require_verification'))
            # Backup Encryption
            if 'backup_require_encryption' in data:
                config.backup_require_encryption = _parse_bool(data.get('backup_require_encryption'))
            # Device Trust (Beta)
            if 'enforce_device_trust' in data:
                config.enforce_device_trust = _parse_bool(data.get('enforce_device_trust'))
            # SMTP / Email
            for _field in ('smtp_host', 'smtp_username', 'smtp_from_email', 'smtp_from_name'):
                if _field in data:
                    setattr(config, _field, str(data.get(_field) or '').strip()[:255])
            if 'smtp_port' in data:
                try:
                    config.smtp_port = max(1, min(65535, int(data['smtp_port'])))
                except (TypeError, ValueError):
                    pass
            if 'smtp_password' in data:
                config.smtp_password = str(data.get('smtp_password') or '').strip()
            if 'smtp_use_tls' in data:
                config.smtp_use_tls = _parse_bool(data.get('smtp_use_tls'))

            # Validate: wildcard requires Cloudflare token
            if config.wildcard_subdomains and config.use_ssl and not config.cloudflare_api_token:
                return Response(
                    {'error': 'Wildcard subdomains require a Cloudflare API Token.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            config.save()

            updated_service_domains = 0
            new_domain = (config.domain or "").strip().lower().rstrip(".")
            if new_domain and new_domain != previous_base_domain:
                updated_service_domains = self._rewrite_service_public_domains(
                    previous_base_domain,
                    new_domain,
                )
                if updated_service_domains:
                    logger.info(
                        "Rewrote %s service public domains from %s to %s",
                        updated_service_domains,
                        previous_base_domain,
                        new_domain,
                    )
            elif original_domain and not new_domain:
                logger.info(
                    "Platform domain cleared from %s; existing service public domains were left unchanged",
                    original_domain,
                )

            # Generate and apply Caddyfile
            try:
                from services.caddy_manager import apply_caddyfile, generate_caddyfile
                caddyfile_content = generate_caddyfile(config)
                cf_token = (config.cloudflare_api_token or "").strip()
                result = apply_caddyfile(
                    caddyfile_content,
                    cloudflare_token=cf_token,
                    preserve_existing_token=not clearing_token,
                )
                config.caddy_status = 'applied' if result['ok'] else 'error'
                config.save(update_fields=['caddy_status'])
                if not result.get('ok'):
                    return Response(
                        {
                            'error': f"Config saved but Caddyfile apply failed: {result.get('message', 'unknown error')}",
                            'caddy_status': config.caddy_status,
                        },
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

                # Auto-create DNS records on Cloudflare when possible.
                # If this raises, the surrounding transaction rolls
                # back the Caddyfile-apply status update, the
                # PlatformConfig changes, and the service-domain
                # rewrites — i.e. nothing is half-applied.
                if config.cloudflare_api_token and config.server_ip and config.domain:
                    try:
                        from apps.deployments.services.dns import ensure_dns_records
                        domains = [config.domain]
                        if config.wildcard_subdomains:
                            domains.append(f"*.{config.domain}")
                        dns_result = ensure_dns_records(domains, config.server_ip, config.cloudflare_api_token)
                        if not dns_result.get("ok"):
                            logger.warning("DNS sync issues: %s", dns_result.get("errors"))
                    except Exception as dns_exc:  # pylint: disable=broad-exception-caught
                        logger.warning("DNS sync skipped: %s", dns_exc)
            except Exception as e:
                config.caddy_status = 'error'
                config.save(update_fields=['caddy_status'])
                return Response(
                    {'error': f'Config saved but Caddyfile apply failed: {e}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            return Response({
                'message': 'Domain configuration updated and Caddyfile applied.',
                'caddy_status': config.caddy_status,
                'cloudflare_api_token_set': bool(config.cloudflare_api_token),
                'updated_service_domains': updated_service_domains,
                'redeploy_required': bool(updated_service_domains),
                'caddyfile_preview': _redact_caddyfile_preview(caddyfile_content),
            })


_CADDYFILE_REDACT_KEYWORDS = (
    'Strict-Transport-Security',
    'tls',
    'internal',
    'basicauth',
    'header Strict-Transport-Security',
)


def _redact_caddyfile_preview(text: str) -> str:
    """Strip any line in a Caddyfile preview that contains a secret or
    internal-only directive. The preview is returned to admins over
    the API, so we must not leak the actual TLS/internal/basicauth
    configuration, nor any ``${ENV_VAR}`` placeholders that may encode
    tokens. Each matching line is replaced wholesale with
    ``***REDACTED***``.

    A line that opens a ``basicauth`` or ``internal`` block also
    redacts all subsequent lines until the matching closing brace.
    """
    if not text:
        return text
    redacted_lines = []
    env_var_re = re.compile(r"\$\{[^}\s]+\}")
    block_open_keywords = ("basicauth", "internal")
    in_secret_block = 0
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            redacted_lines.append(line)
            continue
        lowered = stripped.lower()
        if not in_secret_block and any(
            kw.lower() in lowered for kw in _CADDYFILE_REDACT_KEYWORDS
        ):
            redacted_lines.append('***REDACTED***')
            if any(open_kw in lowered for open_kw in block_open_keywords) and "{" in stripped:
                in_secret_block = stripped.count("{") - stripped.count("}")
            continue
        if in_secret_block:
            redacted_lines.append('***REDACTED***')
            in_secret_block += stripped.count("{") - stripped.count("}")
            in_secret_block = max(in_secret_block, 0)
            continue
        if env_var_re.search(stripped):
            redacted_lines.append('***REDACTED***')
            continue
        redacted_lines.append(line)
    return "\n".join(redacted_lines)


class RouteRecheckView(GenericAPIView):
    """
    Public route recheck hook for fallback pages.

    Allows a domain-level health recheck without requiring a dashboard login.
    This is intentionally rate-limited and only operates on known service domains.
    """

    serializer_class = EmptySerializer
    permission_classes = [permissions.AllowAny]

    def _extract_domain(self, request):
        raw_host = (
            request.query_params.get("host")
            or request.data.get("host")
            or request.get_host()
        )
        host = str(raw_host or "").strip().lower()
        if ":" in host:
            host = host.split(":", 1)[0]
        domain, domain_error = _normalize_request_domain(host)
        if domain_error:
            return None, domain_error
        return domain, None

    def _trigger_recheck(self, service):
        try:
            from apps.deployments.services.health_monitor import (
                _check_service_health,
                reset_restart_state,
            )

            reset_restart_state(str(service.id))
            _check_service_health(service, Deployment)
            service.refresh_from_db(fields=["health_status"])
            return True, service.health_status
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Route recheck failed for service %s: %s", service.id, exc)
            return False, "unknown"

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)

    def _handle(self, request):
        domain, domain_error = self._extract_domain(request)
        if domain_error:
            return Response(
                {"error": f"Invalid domain: {domain_error}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = _service_for_domain(domain)
        if not service:
            return Response(
                {"error": "Domain is not mapped to a service"},
                status=status.HTTP_404_NOT_FOUND,
            )

        client_ip = (
            str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",")[0].strip()
            or str(request.META.get("REMOTE_ADDR", "unknown")).strip()
            or "unknown"
        )
        throttle_key = f"route-recheck:{service.id}:{client_ip}"
        if cache.get(throttle_key):
            return Response(
                {"error": "Recheck already requested. Try again in a few seconds."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        cache.set(throttle_key, True, timeout=20)

        ok, health_status = self._trigger_recheck(service)
        if not ok:
            return Response(
                {"error": "Failed to run health recheck"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "message": "Health recheck triggered",
                "service_id": str(service.id),
                "health_status": health_status,
            }
        )


from .views_route_status import (
    RouteStatusView,  # noqa: F401  (re-export for backwards compat — see views_route_status.py)
)


class ServiceBackupViewSet(viewsets.ModelViewSet):
    queryset = ServiceBackup.objects.all().order_by('-created_at')
    serializer_class = ServiceBackupSerializer
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _user_can_access_service(user, service):
        if not user or not user.is_authenticated or not service:
            return False
        if user.is_superuser or service.owner_id == user.id:
            return True
        return service.project_id and service.project.team_id and service.project.team.members.filter(user=user).exists()

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def get_queryset(self):
        qs = self.queryset

        # `header` and `download_key` are intentionally public actions (see
        # their docstrings) — the data they return (V2 key_id + fingerprint +
        # service name + creation timestamp) is non-secret and meant to be
        # shareable. Those actions override the viewset permission to
        # `permission_classes=[permissions.AllowAny]` and clear
        # `authentication_classes`, so `self.request.user` arrives as
        # `AnonymousUser`. The auth-scoped Q-filter below would then raise
        # `TypeError: Cannot cast AnonymousUser to int` when Django tries
        # to coerce it for the FK lookup. Return the unscoped queryset for
        # those actions instead.
        if getattr(self, 'action', None) in ('header', 'download_key'):
            pass
        elif not self.request.user.is_authenticated:
            # Defensive: any other action reached without auth (shouldn't
            # happen given the viewset's IsAuthenticated default, but cheap
            # to guard against future regressions).
            return qs.none()
        elif self.request.user.is_superuser or is_remote_sync_request(self.request):
            pass
        else:
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()

        qs = qs.order_by('-created_at')
        project_id = self.request.query_params.get('project_id')
        if project_id:
            qs = qs.filter(service__project_id=project_id)
        service_pk = self.kwargs.get('service_pk')
        if service_pk:
            qs = qs.filter(service_id=service_pk)
        return qs

    def perform_create(self, serializer):
        service = serializer.validated_data.get('service')
        if not self._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")
        backup = serializer.save(created_by=self.request.user, status='PENDING')
        create_service_backup_task.delay(service_id=str(backup.service.id), backup_type='MANUAL', backup_id=str(backup.id))

    @action(detail=False, methods=['post'], url_path='import-key')
    def import_key(self, request):
        """Register a foreign BACKUP_ENCRYPTION_KEY on this master for
        cross-master restore. Accepts ``key_id`` (8-char hex from the
        source backup's V2 header) and ``key_material`` (the source's
        Fernet ``BACKUP_ENCRYPTION_KEY`` from ``.env``).

        The action is admin-only and audit-logged. The imported key is
        stored encrypted at rest with ``FIELD_ENCRYPTION_KEY`` and is
        only consulted when the V2 header's ``key_id`` does not match
        this master's active key.
        """
        from .services.backup_service import (
            BackupKeyCollisionError,
            BackupService,
        )
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin only. Use the install.sh on each master to manage keys.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        key_id = str(request.data.get('key_id') or '').strip()
        key_material = str(request.data.get('key_material') or '').strip()
        label = str(request.data.get('label') or '').strip()[:100]
        if not key_id or not key_material:
            return Response(
                {'error': 'Both "key_id" and "key_material" are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = BackupService.import_backup_key(
                key_id=key_id,
                key_material=key_material,
                label=label,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except BackupKeyCollisionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        with contextlib.suppress(Exception):
            AuditLog(
                actor=request.user.get_username(),
                action='BACKUP_KEY_IMPORTED' if result.get('source') == 'IMPORTED' else 'BACKUP_KEY_REIMPORTED',
                target=f'key_id={result["key_id"]}',
                metadata={
                    'fingerprint': result['fingerprint'],
                    'label': label,
                    'created': result.get('created', False),
                },
            ).save()
        return Response(result, status=status.HTTP_201_CREATED if result.get('created') else status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='list-keys')
    def list_keys(self, request):
        """Return stored backup encryption keys (fingerprints only, no key material)."""
        if not request.user.is_superuser:
            return Response({'error': 'Admin access required'}, status=status.HTTP_403_FORBIDDEN)
        from apps.deployments.models_backup import BackupEncryptionKey
        keys = BackupEncryptionKey.objects.all().order_by('-created_at')
        return Response([
            {
                'id': str(k.id),
                'key_id': k.key_id,
                'fingerprint': k.fingerprint,
                'label': k.label,
                'source': k.source,
                'is_active': k.is_active,
                'created_at': k.created_at.isoformat() if k.created_at else None,
            }
            for k in keys
        ])

    @action(detail=False, methods=['post'], url_path='delete-key')
    def delete_key(self, request):
        """Delete a stored backup encryption key by id. Admin only.
        Cannot delete the active (AUTO) key."""
        from apps.deployments.models_backup import BackupEncryptionKey
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin only.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        key_id_param = str(request.data.get('id') or '').strip()
        if not key_id_param:
            return Response(
                {'error': '"id" is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            key = BackupEncryptionKey.objects.get(id=key_id_param)
        except BackupEncryptionKey.DoesNotExist:
            return Response({'error': 'Key not found.'}, status=status.HTTP_404_NOT_FOUND)
        if key.is_active and key.source == 'AUTO':
            return Response(
                {'error': 'Cannot delete the active local encryption key.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        key.delete()
        return Response({'deleted': True, 'id': key_id_param})

    @action(detail=True, methods=['get'], url_path='header')
    def header(self, request, pk=None):
        """Return the V2 backup header (key_id, fingerprint) so the
        operator can copy the key_id to a different master for the
        ``import-key`` flow. Returns 404 if the backup is not in V2
        format. This endpoint is intentionally public — the returned
        data (key_id + fingerprint) is not secret material, and the
        backup is already accessible via a signed download link that
        the operator can share. Requiring auth here would force the
        download-key UI to handle a second auth flow alongside the
        signed download.
        """
        from .services.backup_service import BackupService
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(info)

    @action(detail=True, methods=['get'], url_path='download-key', permission_classes=[permissions.AllowAny], authentication_classes=[])
    def download_key(self, request, pk=None):
        """Download the V2 backup header as a .key.json file alongside
        the backup. The operator stores this file with the backup and
        uses ``POST /api/v1/backups/import-key/`` on the target master
        to import the key before restoring.

        The file is safe to distribute alongside the backup — it
        contains only the public key_id and fingerprint, NOT the
        encryption key material itself. The key material must be
        transferred via a separate secure channel (the
        ``BackupEncryptionKey`` table on the source master, or an
        out-of-band exchange).
        """
        from .services.backup_service import BackupService
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        key_payload = {
            'backup_id': str(backup.id),
            'service_name': getattr(getattr(backup, 'service', None), 'name', None),
            'created_at': backup.created_at.isoformat() if backup.created_at else None,
            'encryption': {
                'format': info.get('format', 'CHUNKED_V2'),
                'key_id': info.get('key_id'),
                'fingerprint': info.get('fingerprint'),
            },
            'usage': (
                'Import this key on the target master with: '
                'POST /api/v1/backups/import-key/ '
                '{"key_id": "<key_id>", "key_material": "<source BACKUP_ENCRYPTION_KEY>"}'
            ),
        }

        import json as _json

        from django.http import HttpResponse
        response = HttpResponse(
            _json.dumps(key_payload, indent=2),
            content_type='application/json',
        )
        filename = f"backup-{backup.id}-key.json"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        backup = self.get_object()

        # Enforce explicit confirmation for destructive actions
        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return Response(
                {'error': 'Explicit confirmation required. Send "confirm": true.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        target_service_id = request.data.get('target_service_id')

        if target_service_id:
            target_service = Service.objects.filter(
                id=target_service_id,
            ).select_related('project__team').first()
            if not self._user_can_access_service(request.user, target_service):
                return Response(
                    {'error': 'Target service not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            target_service = backup.service

        guard = ServerGuard.check_user_workload_allowed(getattr(target_service, 'server', None))
        if not guard["ok"]:
            return Response(guard, status=status.HTTP_400_BAD_REQUEST)

        # ── Pre-flight: verify the encryption key is available ───────
        # If the backup is encrypted and we don't have the key (either
        # the local BACKUP_ENCRYPTION_KEY env var, a matching imported
        # key, or a key supplied in this request), refuse to queue
        # the task. Without this, the task would fail silently inside
        # the Celery worker (UnknownBackupKeyIdError), and the user
        # would see 'restore_started' followed by no progress.
        key_provided = _resolve_encryption_key(request)
        if backup.file_path and backup.file_path.endswith('.enc'):
            from .services.backup_service import (
                BackupService,
            )
            # Check metadata stamp first (works for cloud-stored backups
            # where the local file doesn't exist yet), then fall back
            # to reading the V2 header from the file on disk.
            enc_meta = (backup.metadata or {}).get('encryption', {})
            meta_key_id = enc_meta.get('key_id', '')
            meta_fingerprint = enc_meta.get('fingerprint', '')
            # If metadata carries the key identity and we have a stored
            # key matching that fingerprint (either the active local key
            # or an imported key), the pre-flight passes without needing
            # the file on disk.
            meta_matched = False
            if meta_fingerprint:
                from .services.backup_service import BackupService as BSC
                if key_provided:
                    try:
                        if BSC.compute_backup_key_fingerprint(key_provided) == meta_fingerprint:
                            meta_matched = True
                    except Exception:
                        pass
                if not meta_matched and BSC.lookup_key_by_id(meta_fingerprint):
                    meta_matched = True
                if not meta_matched and BSC.lookup_key_by_id(meta_key_id):
                    meta_matched = True
            if not meta_matched and not BackupService.can_decrypt_backup(
                backup.file_path, passed_key=key_provided,
            ):
                header_key_id = meta_key_id or 'unknown'
                # Try reading the header from the file for a better key_id
                try:
                    header = BackupService.read_v2_header(backup.file_path)
                    header_key_id = header.get('key_id', header_key_id)
                except (OSError, ValueError):
                    pass
                return Response(
                    {
                        'error': (
                            'Encryption key required. This backup '
                            'was encrypted on a different '
                            'master. Import the key or '
                            'provide it in the request.'
                        ),
                        'error_code': 'ENCRYPTION_KEY_REQUIRED',
                        'key_id': header_key_id,
                        'remediation': (
                            'POST /api/v1/backups/import-key/ with '
                            'key_id and key_material from '
                            'the source master, or send '
                            '"encryption_key" in the '
                            'restore request body, or '
                            'upload a key_file JSON.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        # ── Pre-flight safety snapshot check ─────────────
        # Attempt a synchronous PRE_TRANSFER snapshot. If it fails, warn
        # the user and ask them to confirm with "force": true. Without a
        # safety snapshot, a corrupt restore loses the active state
        # permanently.
        force = str(request.data.get('force', '')).lower() == 'true'
        if not force:
            try:
                from .services.backup_service import BackupService
                BackupService().backup_service(
                    target_service.id, backup_type='PRE_TRANSFER',
                )
            except Exception as snap_exc:
                logger.warning(
                    "Pre-restore snapshot failed for service %s: %s",
                    target_service.id, snap_exc,
                )
                return Response(
                    {
                        'error': (
                            'Pre-restore safety snapshot could not be '
                            'created. Proceeding without a snapshot will '
                            'permanently destroy the current running state '
                            'if the restore archive is corrupt.'
                        ),
                        'snapshot_error': str(snap_exc),
                        'backup_id': str(backup.id),
                        'remediation': (
                            'Fix the snapshot error and retry, or send '
                            '"force": true to proceed without a safety '
                            'snapshot. Use force with caution — data '
                            'loss is possible if the backup is corrupt.'
                        ),
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(target_service_id) if target_service_id else None,
            requesting_user_id=request.user.id,
            raise_on_snapshot_failure=not force,
            encryption_key=key_provided or None,
        )
        return Response({'status': 'restore_started'})

    @action(detail=False, methods=['post'], url_path='list-backups')
    def list_cloud_backups(self, request):
        """List available backup files in a cloud storage bucket (service scope)."""
        cloud_storage_id = request.data.get('cloud_storage_id', '').strip()
        prefix = request.data.get('prefix', 'smsly-backups/').strip()
        service_id = request.data.get('service_id', '').strip()

        if not cloud_storage_id:
            return Response(
                {'error': 'cloud_storage_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.deployments.models_cloud_storage import CloudStorageDestination

        try:
            dest = CloudStorageDestination.objects.get(id=cloud_storage_id)
        except CloudStorageDestination.DoesNotExist:
            return Response(
                {'error': 'Cloud storage destination not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Scope: only platform-wide destinations or same-service destinations
        if dest.service is not None and str(dest.service.id) != service_id:
            return Response(
                {'error': 'This cloud destination belongs to a different service.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .services.backup_service import list_s3_objects

        objects = list_s3_objects(
            bucket=dest.bucket,
            prefix=prefix,
            endpoint=dest.endpoint,
            region=dest.region,
            access_key=dest.access_key,
            secret_key=dest.secret_key,
        )

        return Response({'objects': objects, 'bucket': dest.bucket})

    @action(detail=False, methods=['post'], url_path='restore-from-cloud')
    def restore_from_cloud(self, request):
        """Restore a service backup directly from cloud storage."""
        cloud_storage_id = request.data.get('cloud_storage_id')
        s3_key = request.data.get('s3_key', '').strip()
        service_id = request.data.get('service_id')

        if not service_id:
            return Response({'error': 'Missing required service_id.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            target_service = Service.objects.get(id=service_id)
            if not self._user_can_access_service(request.user, target_service):
                return Response({'error': 'Target service not found or access denied.'}, status=status.HTTP_404_NOT_FOUND)
        except Service.DoesNotExist:
            return Response({'error': 'Target service not found.'}, status=status.HTTP_404_NOT_FOUND)

        if cloud_storage_id:
            from apps.deployments.models_cloud_storage import CloudStorageDestination
            try:
                dest = CloudStorageDestination.objects.get(id=cloud_storage_id)
                # Scope: only platform-wide or same-service destinations
                if dest.service is not None and str(dest.service.id) != service_id:
                    return Response(
                        {'error': 'This cloud destination belongs to a different service.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                s3_bucket = dest.bucket
                endpoint = dest.endpoint
                region = dest.region
                access_key = dest.access_key
                secret_key = dest.secret_key
            except CloudStorageDestination.DoesNotExist:
                return Response({'error': 'Cloud storage destination not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            s3_bucket = request.data.get('s3_bucket', '').strip()
            endpoint = request.data.get('s3_endpoint', '').strip()
            region = request.data.get('s3_region', 'us-east-1').strip()
            access_key = request.data.get('s3_access_key', '').strip()
            secret_key = request.data.get('s3_secret_key', '').strip()

        from .services.backup_service import BackupService, download_from_s3, normalize_s3_key
        s3_key = normalize_s3_key(s3_key, s3_bucket)

        if not s3_bucket or not s3_key or not access_key or not secret_key:
            return Response({'error': 'Missing required S3 configuration fields or cloud_storage_id.'}, status=status.HTTP_400_BAD_REQUEST)
        import uuid as _uuid
        dest_filename = f"cloud_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        backups_dir = os.path.join('/app', 'backups', 'services', str(service_id))
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, dest_filename)

        if not download_from_s3(s3_bucket, s3_key, dest_path, endpoint=endpoint, region=region, access_key=access_key, secret_key=secret_key):
            return Response({'error': 'Failed to download backup from cloud storage. Check credentials and key.'}, status=status.HTTP_400_BAD_REQUEST)

        svc = BackupService()
        try:
            dest_path = svc._maybe_encrypt(dest_path)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            return Response({'error': f'Failed to encrypt downloaded backup: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        file_size = os.path.getsize(dest_path)
        backup = ServiceBackup.objects.create(
            service=target_service,
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            backup_type='MANUAL',
            error_message=f'Restored from cloud: {s3_bucket}/{s3_key}',
        )

        # ── Pre-flight safety snapshot check ─────────────
        force = str(request.data.get('force', '')).lower() == 'true'
        if not force:
            try:
                BackupService().backup_service(
                    target_service.id, backup_type='PRE_TRANSFER',
                )
            except Exception as snap_exc:
                logger.warning(
                    "Pre-restore snapshot failed for service %s during cloud restore: %s",
                    target_service.id, snap_exc,
                )
                with contextlib.suppress(OSError):
                    os.remove(dest_path)
                backup.status = 'FAILED'
                backup.error_message = f"Pre-restore snapshot failed: {snap_exc}"
                backup.save(update_fields=['status', 'error_message'])
                return Response(
                    {
                        'error': (
                            'Pre-restore safety snapshot could not be '
                            'created. Proceeding without a snapshot will '
                            'permanently destroy the current running state '
                            'if the restore archive is corrupt.'
                        ),
                        'snapshot_error': str(snap_exc),
                        'backup_id': str(backup.id),
                        'remediation': (
                            'Fix the snapshot error and retry, or send '
                            '"force": true to proceed without a safety '
                            'snapshot. Use force with caution — data '
                            'loss is possible if the backup is corrupt.'
                        ),
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

        encryption_key = _resolve_encryption_key(request)
        from apps.deployments.tasks import restore_service_backup_task
        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(service_id),
            requesting_user_id=request.user.id,
            encryption_key=encryption_key,
            raise_on_snapshot_failure=not force,
        )

        return Response({
            'status': 'Restore started from cloud backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })

    @action(detail=True, methods=['get'], permission_classes=[permissions.AllowAny], authentication_classes=[])
    def download(self, request, pk=None):
        signed_value = request.query_params.get('signed')
        token_value = request.query_params.get('token')
        if token_value:
            return Response({'error': 'Raw token auth is disabled; use a signed download link.'}, status=status.HTTP_401_UNAUTHORIZED)
        if signed_value:
            if not _verify_signed_download(signed_value, str(pk)):
                return Response({'error': 'Invalid or expired download link'}, status=status.HTTP_401_UNAUTHORIZED)
        elif not request.user.is_authenticated:
            return Response({'error': 'Authentication credentials were not provided.'}, status=status.HTTP_401_UNAUTHORIZED)
        elif not request.user.is_superuser:
            # Server backups contain the full platform state — only
            # superusers may download them without a signed URL.
            return Response(
                {'error': 'Not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Bypass get_queryset() which filters by request.user — signed/AllowAny
        # requests have an AnonymousUser that crashes the queryset filter.
        backup = self.queryset.model.objects.filter(pk=pk).first()
        if not backup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        # Ownership gate: authenticated downloaders must own the backup's service.
        # Signed URLs bypass this (already verified above). Without this, an
        # authenticated user could brute-force UUIDs and download any backup.
        if not signed_value and request.user.is_authenticated:
            if not self._user_can_access_service(request.user, backup.service):
                return Response(
                    {'error': 'Not found'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        file_path = backup.file_path

        if not file_path or not os.path.exists(file_path):
            # File missing locally — try to download from cloud storage
            from .services.backup_service import _download_backup_from_cloud
            if getattr(backup, 'cloud_uploaded', False):
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                if _download_backup_from_cloud(backup, file_path):
                    logger.info("Downloaded backup %s from cloud to %s", backup.id, file_path)
                else:
                    return Response({'error': 'Backup file not found locally and cloud download failed.'}, status=status.HTTP_404_NOT_FOUND)
            else:
                return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)

        from .services.backup_service import BackupService, UnknownBackupKeyIdError
        key = BackupService._get_encryption_key()

        # If the file is encrypted, we must decrypt it for the user to download
        if file_path.endswith('.enc') and key:
            try:
                decrypted_path = BackupService.decrypt_backup(file_path, key)
                return _open_backup_download_response(
                    request,
                    decrypted_path,
                    os.path.basename(file_path).replace(".enc", ""),
                    cleanup_path=decrypted_path,
                )
            except UnknownBackupKeyIdError as exc:
                return Response(
                    {
                        'error': str(exc),
                        'key_id': exc.key_id,
                        'fingerprint': exc.fingerprint,
                        'remediation': (
                            'POST /api/v1/backups/service/import-key/ with '
                            'key_id and key_material from the source master, '
                            'then retry this download.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to decrypt backup for download: {e}")
                return Response({'error': 'Failed to decrypt backup for download.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return _open_backup_download_response(
            request,
            file_path,
            os.path.basename(file_path),
        )

    @action(detail=True, methods=['get'], url_path='download-url')
    def download_url(self, request, pk=None):
        backup = self.get_object()
        return Response({'url': _generate_signed_download_url(request, str(backup.id), 'backup-download', path_params={'pk': str(backup.id)})})

    # ── Verify integrity ────────────────────────────────────────────
    @action(detail=True, methods=['post'], url_path='verify')
    def verify(self, request, pk=None):
        """POST /api/v1/backups/{id}/verify/

        Runs integrity verification (checksum + archive validity) on
        this backup synchronously and returns the result immediately.
        """
        import hashlib as _hashlib
        import os as _os
        import tarfile as _tarfile

        backup = self.get_object()
        filepath = backup.file_path
        errors = []
        passed = False

        try:
            if not filepath or not _os.path.exists(filepath):
                raise FileNotFoundError(f"Backup file not found: {filepath}")

            expected_hash = (getattr(backup, 'metadata', None) or {}).get('checksum_sha256', '')
            if expected_hash:
                sha = _hashlib.sha256()
                with open(filepath, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b''):
                        sha.update(chunk)
                if sha.hexdigest() != expected_hash:
                    raise ValueError("Checksum mismatch — backup may be corrupted")

            with _tarfile.open(filepath, 'r:gz') as tar:
                members = tar.getmembers()
                if not members:
                    raise ValueError("Archive is empty")

            passed = True
        except Exception as exc:
            errors.append(str(exc))

        return Response({
            'status': 'completed',
            'backup_id': str(backup.id),
            'passed': passed,
            'errors': errors,
        })

    # ── Restore from local file upload ──────────────────────────────
    @action(detail=False, methods=['post'], url_path='upload-restore')
    def upload_restore(self, request):
        """POST /api/v1/backups/upload-restore/

        Upload a backup tar.gz file and restore it to a service.
        Body: multipart/form-data with ``file`` and ``service_id``.
        """
        file = request.FILES.get('file')
        service_id = request.data.get('service_id')
        if not file or not service_id:
            return Response({'error': 'file and service_id are required.'}, status=status.HTTP_400_BAD_REQUEST)

        MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
        if file.size > MAX_UPLOAD_SIZE:
            return Response(
                {'error': f'File too large. Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)}MB.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            target_service = Service.objects.get(id=service_id)
            if not self._user_can_access_service(request.user, target_service):
                return Response({'error': 'Service not found or access denied.'}, status=status.HTTP_404_NOT_FOUND)
        except Service.DoesNotExist:
            return Response({'error': 'Service not found.'}, status=status.HTTP_404_NOT_FOUND)

        import uuid as _uuid
        dest_filename = f"local_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        backups_dir = os.path.join('/app', 'backups', 'services', str(service_id))
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, dest_filename)

        with open(dest_path, 'wb+') as f:
            for chunk in file.chunks():
                f.write(chunk)

        file_size = os.path.getsize(dest_path)
        from .services.backup_service import BackupService
        svc = BackupService()
        try:
            dest_path = svc._maybe_encrypt(dest_path)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            return Response({'error': f'Failed to process uploaded backup: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        backup = ServiceBackup.objects.create(
            service=target_service,
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            backup_type='MANUAL',
            error_message=f'Restored from local upload: {file.name}',
        )

        encryption_key = _resolve_encryption_key(request)
        from apps.deployments.tasks import restore_service_backup_task
        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(service_id),
            requesting_user_id=request.user.id,
            encryption_key=encryption_key,
            raise_on_snapshot_failure=False,
        )

        return Response({
            'status': 'restore_started',
            'backup_id': str(backup.id),
            'file_name': file.name,
        })

    # ── Restoration history ─────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='restore-history')
    def restore_history(self, request):
        """GET /api/v1/backups/restore-history/

        Returns backups that were used in a restore (have restore
        metadata in error_message), plus their associated deployment
        status.  Useful for showing a "Restoration Activity" timeline.
        """
        qs = ServiceBackup.objects.filter(
            get_team_q_filter(request.user, prefix='service__', request=request)
        ).filter(
            # Restore-related backups have specific markers in error_message
            error_message__icontains='restored'
        ).order_by('-created_at')[:20]

        results = []
        for b in qs:
            deployment = b.service.deployments.filter(
                created_at__gte=b.created_at
            ).order_by('created_at').first() if b.service_id else None

            results.append({
                'backup_id': str(b.id),
                'service_id': str(b.service_id) if b.service_id else None,
                'service_name': b.service.name if b.service else None,
                'restored_at': b.created_at.isoformat() if b.created_at else None,
                'restore_type': b.error_message or 'Unknown',
                'deployment_status': deployment.status if deployment else None,
                'deployment_id': str(deployment.id) if deployment else None,
            })
        return Response(results)


class ServiceSnapshotViewSet(viewsets.ModelViewSet):
    queryset = ServiceSnapshot.objects.all().order_by('-created_at')
    serializer_class = ServiceSnapshotSerializer
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _user_can_access_service(user, service):
        if not user or not user.is_authenticated or not service:
            return False
        if user.is_superuser or service.owner_id == user.id:
            return True
        return service.project_id and service.project.team_id and service.project.team.members.filter(user=user).exists()

    def get_queryset(self):
        qs = self.queryset
        if not self.request.user.is_authenticated:
            return qs.none()

        if not (self.request.user.is_superuser or is_remote_sync_request(self.request)):
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()

        project_id = self.request.query_params.get('project_id')
        if project_id:
            qs = qs.filter(service__project_id=project_id)
        service_pk = self.kwargs.get('service_pk')
        if service_pk:
            qs = qs.filter(service_id=service_pk)
        return qs

    def perform_create(self, serializer):
        service = serializer.validated_data.get('service')
        if not self._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")

        from .services.snapshot_service import SnapshotService
        try:
            snapshot = SnapshotService.capture_snapshot(
                service_id=str(service.id),
                trigger=serializer.validated_data.get('trigger', 'MANUAL'),
                label=serializer.validated_data.get('label', ''),
                created_by=self.request.user,
            )
            serializer.instance = snapshot
        except Exception as exc:
            raise serializers.ValidationError({"detail": str(exc)})

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None, *args, **kwargs):
        snapshot = self.get_object()

        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return Response(
                {'error': 'Explicit confirmation required. Send "confirm": true.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ServiceSnapshotRestoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_service_id = serializer.validated_data.get('target_service_id')
        redeploy = serializer.validated_data.get('redeploy', False)

        if target_service_id:
            target_service = Service.objects.filter(
                id=target_service_id,
            ).select_related('project__team').first()
            if not self._user_can_access_service(request.user, target_service):
                return Response(
                    {'error': 'Target service not found or permission denied'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            target_service = snapshot.service

        if not self._user_can_access_service(request.user, target_service):
            return Response(
                {'error': 'Permission denied for target service'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .services.snapshot_service import SnapshotService
        try:
            result = SnapshotService.restore_snapshot(
                snapshot_id=str(snapshot.id),
                target_service_id=str(target_service.id) if target_service_id else None,
                redeploy=redeploy,
                requesting_user=request.user,
            )
            with contextlib.suppress(Exception):
                AuditLog(
                    actor=request.user.get_username(),
                    action='SNAPSHOT_RESTORED',
                    target=f'snapshot={snapshot.id}',
                    metadata={
                        'service_id': str(target_service.id),
                        'redeploy': redeploy,
                        'changes_count': result.get('config_changes', 0),
                    },
                ).save()
            return Response(result, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='diff')
    def diff(self, request, pk=None, *args, **kwargs):
        snapshot_a = self.get_object()
        serializer = ServiceSnapshotDiffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        compare_with_id = serializer.validated_data['compare_with_id']
        try:
            snapshot_b = ServiceSnapshot.objects.get(id=compare_with_id)
        except ServiceSnapshot.DoesNotExist:
            return Response({'error': 'Comparison snapshot not found'}, status=status.HTTP_404_NOT_FOUND)

        if not self._user_can_access_service(request.user, snapshot_a.service) or \
           not self._user_can_access_service(request.user, snapshot_b.service):
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        from .services.snapshot_service import SnapshotService
        try:
            result = SnapshotService.diff_snapshots(
                snapshot_a_id=str(snapshot_a.id),
                snapshot_b_id=str(snapshot_b.id),
            )
            return Response(result, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ServerBackupViewSet(viewsets.ModelViewSet):
    serializer_class = ServerBackupSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = ServerBackup.objects.all().order_by('-created_at')

    def perform_create(self, serializer):
        backup = serializer.save(status='PENDING')
        create_server_backup_task.delay(backup_id=str(backup.id))

    @action(detail=True, methods=['get'], url_path='download-key', permission_classes=[permissions.AllowAny], authentication_classes=[])
    def download_key(self, request, pk=None):
        """Download the V2 backup header as a .key.json file. See
        ServiceBackupViewSet.download_key for details. Public for the
        same reason — key_id/fingerprint are not secret material.
        """
        import json as _json

        from django.http import HttpResponse

        from .services.backup_service import BackupService
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        key_payload = {
            'backup_id': str(backup.id),
            'scope': 'server',
            'created_at': backup.created_at.isoformat() if backup.created_at else None,
            'encryption': {
                'format': info.get('format', 'CHUNKED_V2'),
                'key_id': info.get('key_id'),
                'fingerprint': info.get('fingerprint'),
            },
            'usage': (
                'Import this key on the target master with: '
                'POST /api/v1/backups/import-key/ '
                '{"key_id": "<key_id>", "key_material": "<source BACKUP_ENCRYPTION_KEY>"}'
            ),
        }
        response = HttpResponse(
            _json.dumps(key_payload, indent=2),
            content_type='application/json',
        )
        response['Content-Disposition'] = f'attachment; filename="backup-{backup.id}-key.json"'
        return response

    @action(detail=False, methods=['post'], url_path='import-key')
    def import_key(self, request):
        """Server-wide counterpart of :meth:`ServiceBackupViewSet.import_key`.
        Same admin-only + audit-logged + cross-master restore flow.
        """
        from .services.backup_service import (
            BackupKeyCollisionError,
            BackupService,
        )
        if not request.user.is_superuser:
            return Response(
                {'error': 'Admin only.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        key_id = str(request.data.get('key_id') or '').strip()
        key_material = str(request.data.get('key_material') or '').strip()
        label = str(request.data.get('label') or '').strip()[:100]
        if not key_id or not key_material:
            return Response(
                {'error': 'Both "key_id" and "key_material" are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = BackupService.import_backup_key(
                key_id=key_id,
                key_material=key_material,
                label=label,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except BackupKeyCollisionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        with contextlib.suppress(Exception):
            AuditLog(
                actor=request.user.get_username(),
                action='BACKUP_KEY_IMPORTED' if result.get('source') == 'IMPORTED' else 'BACKUP_KEY_REIMPORTED',
                target=f'key_id={result["key_id"]}',
                metadata={
                    'fingerprint': result['fingerprint'],
                    'label': label,
                    'created': result.get('created', False),
                },
            ).save()
        return Response(result, status=status.HTTP_201_CREATED if result.get('created') else status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='header')
    def header(self, request, pk=None):
        """Server-wide counterpart of :meth:`ServiceBackupViewSet.header`."""
        from .services.backup_service import BackupService
        backup = self.get_object()
        if not backup.file_path or not os.path.exists(backup.file_path):
            return Response({'error': 'Backup file not found'}, status=status.HTTP_404_NOT_FOUND)
        try:
            info = BackupService.read_v2_header(backup.file_path)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(info)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        backup = self.get_object()

        # Enforce explicit confirmation for destructive actions
        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return Response(
                {'error': 'Explicit confirmation required. Send "confirm": true.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if backup.status != 'COMPLETED':
            return Response({'error': 'Only COMPLETED backups can be restored.'}, status=status.HTTP_400_BAD_REQUEST)

        # Pre-flight: verify encryption key is available for cross-master restores
        key_provided = _resolve_encryption_key(request)
        if backup.file_path and backup.file_path.endswith('.enc'):
            from .services.backup_service import (
                BackupService,
            )
            if not BackupService.can_decrypt_backup(backup.file_path, passed_key=key_provided):
                try:
                    header = BackupService.read_v2_header(backup.file_path)
                    header_key_id = header.get('key_id', 'unknown')
                    return Response(
                        {
                            'error': (
                                'Encryption key required. This backup '
                                'was encrypted on a different master. '
                                'Import the key or provide it in the request.'
                            ),
                            'error_code': 'ENCRYPTION_KEY_REQUIRED',
                            'key_id': header_key_id,
                            'remediation': (
                                'POST /api/v1/server/backups/import-key/ with '
                                'key_id and key_material from the source master, '
                                'or send "encryption_key" in the restore request body, '
                                'or upload a key_file JSON.'
                            ),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                except (OSError, ValueError):
                    pass

        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(
            backup_id=str(backup.id),
            encryption_key=key_provided or None,
            requesting_user_id=request.user.id,
        )
        return Response({
            'status': 'restore_started',
            'volume_restore_required': True,
            'volume_warning': (
                "Docker volumes are NOT restored during server restores. "
                "Volumes live on the host filesystem and will be re-attached "
                "on the next deployment of each service. If volume data was "
                "also lost, restore it from service-level backups or filesystem "
                "snapshots."
            ),
        })

    @action(detail=True, methods=['get'], permission_classes=[permissions.AllowAny], authentication_classes=[])
    def download(self, request, pk=None):
        signed_value = request.query_params.get('signed')
        token_value = request.query_params.get('token')
        if token_value:
            return Response({'error': 'Raw token auth is disabled; use a signed download link.'}, status=status.HTTP_401_UNAUTHORIZED)
        if signed_value:
            if not _verify_signed_download(signed_value, str(pk)):
                return Response({'error': 'Invalid or expired download link'}, status=status.HTTP_401_UNAUTHORIZED)
        elif not request.user.is_authenticated:
            return Response({'error': 'Authentication credentials were not provided.'}, status=status.HTTP_401_UNAUTHORIZED)

        # Bypass get_queryset() which filters by request.user — signed/AllowAny
        # requests have an AnonymousUser that crashes the queryset filter.
        backup = self.queryset.model.objects.filter(pk=pk).first()
        if not backup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        file_path = backup.file_path

        if not file_path or not os.path.exists(file_path):
            # File missing locally — try to download from cloud storage
            from .services.backup_service import _download_backup_from_cloud
            if getattr(backup, 'cloud_uploaded', False):
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                if _download_backup_from_cloud(backup, file_path):
                    logger.info("Downloaded server backup %s from cloud to %s", backup.id, file_path)
                else:
                    return Response({'error': 'Backup file not found on disk and cloud download failed.'}, status=status.HTTP_404_NOT_FOUND)
            else:
                return Response({'error': 'Backup file not found on disk.'}, status=status.HTTP_404_NOT_FOUND)

        from .services.backup_service import BackupService, UnknownBackupKeyIdError
        key = BackupService._get_encryption_key()

        # If the file is encrypted, we must decrypt it for the user to download
        if file_path.endswith('.enc') and key:
            try:
                decrypted_path = BackupService.decrypt_backup(file_path, key)

                return _open_backup_download_response(
                    request,
                    decrypted_path,
                    os.path.basename(file_path).replace(".enc", ""),
                    cleanup_path=decrypted_path,
                )
            except UnknownBackupKeyIdError as exc:
                return Response(
                    {
                        'error': str(exc),
                        'key_id': exc.key_id,
                        'fingerprint': exc.fingerprint,
                        'remediation': (
                            'POST /api/v1/backups/server/import-key/ with '
                            'key_id and key_material from the source master, '
                            'then retry this download.'
                        ),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Failed to decrypt backup for download: {e}")
                return Response({'error': 'Failed to decrypt backup for download.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return _open_backup_download_response(
            request,
            file_path,
            os.path.basename(file_path),
        )

    @action(detail=True, methods=['get'], url_path='download-url')
    def download_url(self, request, pk=None):
        backup = self.get_object()
        return Response({'url': _generate_signed_download_url(request, str(backup.id), 'server-backup-download', path_params={'pk': str(backup.id)})})

    @action(detail=False, methods=['post'], url_path='upload-restore',
            parser_classes=[parsers.MultiPartParser])
    def upload_restore(self, request):
        """Accept a backup .tar.gz file upload and restore from it."""
        uploaded = request.FILES.get('file')
        if not uploaded:
            return Response({'error': 'No file uploaded. Send a .tar.gz file as "file".'}, status=status.HTTP_400_BAD_REQUEST)

        if not uploaded.name.endswith(('.tar.gz', '.tgz')):
            return Response({'error': 'File must be a .tar.gz or .tgz archive.'}, status=status.HTTP_400_BAD_REQUEST)

        import uuid as _uuid

        from apps.deployments.services.backup_service import BackupService

        # Save uploaded file to shared backup volume
        backups_dir = BackupService._get_backups_dir('server')
        dest_filename = f"uploaded_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        dest_path = os.path.join(backups_dir, dest_filename)

        with open(dest_path, 'wb') as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        # Enforce at-rest encryption policy. If BACKUP_REQUIRE_ENCRYPTION
        # is set, refuse to store an unencrypted uploaded backup.
        svc = BackupService()
        try:
            dest_path = svc._maybe_encrypt(dest_path)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            return Response(
                {'error': f'Failed to encrypt uploaded backup: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        file_size = os.path.getsize(dest_path)

        # Create a ServerBackup record pointing to the uploaded file
        backup = ServerBackup.objects.create(
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            error_message=f'Uploaded restore from: {uploaded.name}',
        )

        # Accept optional encryption key for cross-master restore
        encryption_key = _resolve_encryption_key(request)

        # Trigger async restore
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(
            backup_id=str(backup.id),
            encryption_key=encryption_key,
            requesting_user_id=request.user.id,
        )

        return Response({
            'status': 'Restore started from uploaded backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })

    @action(detail=False, methods=['post'], url_path='list-backups')
    def list_cloud_backups(self, request):
        """List available backup files in a cloud storage bucket (server scope)."""
        cloud_storage_id = request.data.get('cloud_storage_id', '').strip()
        prefix = request.data.get('prefix', 'smsly-backups/').strip()

        if not cloud_storage_id:
            return Response(
                {'error': 'cloud_storage_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.deployments.models_cloud_storage import CloudStorageDestination

        try:
            dest = CloudStorageDestination.objects.get(id=cloud_storage_id)
        except CloudStorageDestination.DoesNotExist:
            return Response(
                {'error': 'Cloud storage destination not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Server-level backups must use platform-wide destinations only
        if dest.service is not None:
            return Response(
                {'error': 'Server backups require a platform-wide cloud destination (no service binding).'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from .services.backup_service import list_s3_objects

        objects = list_s3_objects(
            bucket=dest.bucket,
            prefix=prefix,
            endpoint=dest.endpoint,
            region=dest.region,
            access_key=dest.access_key,
            secret_key=dest.secret_key,
        )

        return Response({'objects': objects, 'bucket': dest.bucket})

    @action(detail=False, methods=['post'], url_path='restore-from-cloud')
    def restore_from_cloud(self, request):
        """Restore a server backup directly from cloud storage."""
        import uuid as _uuid

        from .services.backup_service import BackupService, download_from_s3, normalize_s3_key

        cloud_storage_id = request.data.get('cloud_storage_id')
        s3_key = request.data.get('s3_key', '').strip()

        if cloud_storage_id:
            from apps.deployments.models_cloud_storage import CloudStorageDestination
            try:
                dest = CloudStorageDestination.objects.get(id=cloud_storage_id)
                # Server-level restores must use platform-wide destinations
                if dest.service is not None:
                    return Response(
                        {'error': 'Server restores require a platform-wide cloud destination (no service binding).'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                s3_bucket = dest.bucket
                endpoint = dest.endpoint
                region = dest.region
                access_key = dest.access_key
                secret_key = dest.secret_key
            except CloudStorageDestination.DoesNotExist:
                return Response({'error': 'Cloud storage destination not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            s3_bucket = request.data.get('s3_bucket', '').strip()
            endpoint = request.data.get('s3_endpoint', '').strip()
            region = request.data.get('s3_region', 'us-east-1').strip()
            access_key = request.data.get('s3_access_key', '').strip()
            secret_key = request.data.get('s3_secret_key', '').strip()

        s3_key = normalize_s3_key(s3_key, s3_bucket)

        if not s3_bucket or not s3_key or not access_key or not secret_key:
            return Response({'error': 'Missing required S3 configuration fields or cloud_storage_id.'}, status=status.HTTP_400_BAD_REQUEST)
        dest_filename = f"cloud_restore_{_uuid.uuid4().hex[:8]}.tar.gz"
        backups_dir = os.path.join('/app', 'backups', 'server')
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, dest_filename)

        if not download_from_s3(s3_bucket, s3_key, dest_path, endpoint=endpoint, region=region, access_key=access_key, secret_key=secret_key):
            return Response({'error': 'Failed to download backup from cloud storage. Check credentials and key.'}, status=status.HTTP_400_BAD_REQUEST)

        svc = BackupService()
        try:
            dest_path = svc._maybe_encrypt(dest_path)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.remove(dest_path)
            return Response({'error': f'Failed to encrypt downloaded backup: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        file_size = os.path.getsize(dest_path)
        backup = ServerBackup.objects.create(
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            error_message=f'Restored from cloud: {s3_bucket}/{s3_key}',
        )

        encryption_key = _resolve_encryption_key(request)
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(
            backup_id=str(backup.id),
            encryption_key=encryption_key,
            requesting_user_id=request.user.id,
        )

        return Response({
            'status': 'Restore started from cloud backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })

class SnapshotScheduleViewSet(viewsets.ModelViewSet):
    queryset = SnapshotSchedule.objects.all().order_by('id')
    serializer_class = SnapshotScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        if instance.is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can delete server-wide snapshot schedules.")
        if instance.service:
            from apps.deployments.views import ServiceBackupViewSet
            if not ServiceBackupViewSet._user_can_access_service(self.request.user, instance.service):
                raise PermissionDenied("You do not have access to this service.")
        super().perform_destroy(instance)

    def get_queryset(self):
        qs = self.queryset
        if not (self.request.user.is_superuser or is_remote_sync_request(self.request)):
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()
        service_id = self.request.query_params.get('service')
        if service_id:
            qs = qs.filter(service_id=service_id)
        return qs

    def _validate_schedule_access(self, serializer):
        service = serializer.validated_data.get(
            'service',
            getattr(serializer.instance, 'service', None),
        )
        is_server_wide = serializer.validated_data.get(
            'is_server_wide',
            getattr(serializer.instance, 'is_server_wide', False),
        )
        if is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can manage server-wide snapshot schedules.")
        if not service and not is_server_wide:
            raise PermissionDenied("A service is required for non-server-wide snapshot schedules.")
        if service and not ServiceBackupViewSet._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")

    def perform_create(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()


class BackupScheduleViewSet(viewsets.ModelViewSet):
    queryset = BackupSchedule.objects.all().order_by('id')
    serializer_class = BackupScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Mirror _validate_schedule_access for the delete path.
        if instance.is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can delete server-wide backup schedules.")
        if instance.service:
            from apps.deployments.views import ServiceBackupViewSet
            if not ServiceBackupViewSet._user_can_access_service(self.request.user, instance.service):
                raise PermissionDenied("You do not have access to this service.")
        super().perform_destroy(instance)

    def get_queryset(self):
        qs = self.queryset
        if not (self.request.user.is_superuser or is_remote_sync_request(self.request)):
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()
        service_id = self.request.query_params.get('service')
        if service_id:
            qs = qs.filter(service_id=service_id)
        return qs

    def _validate_schedule_access(self, serializer):
        service = serializer.validated_data.get(
            'service',
            getattr(serializer.instance, 'service', None),
        )
        is_server_wide = serializer.validated_data.get(
            'is_server_wide',
            getattr(serializer.instance, 'is_server_wide', False),
        )
        if is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can manage server-wide backup schedules.")
        if not service and not is_server_wide:
            raise PermissionDenied("A service is required for non-server-wide backup schedules.")
        if service and not ServiceBackupViewSet._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")

    def perform_create(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()


class PlatformResourcesView(GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        import shutil
        import socket

        import psutil

        from apps.deployments.models import ManagedServer, Service
        vm = psutil.virtual_memory()
        disk = shutil.disk_usage("/")
        load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
        services = Service.objects.all()
        if not request.user.is_superuser:
            services = services.filter(owner=request.user)
        running = services.filter(deployments__status=Deployment.Status.ACTIVE).distinct().count()
        failed = services.filter(deployments__status=Deployment.Status.FAILED).distinct().count()
        servers = ManagedServer.objects.all()
        if not request.user.is_superuser:
            servers = servers.filter(owner=request.user)
        nodes = [{
            "id": str(s.id),
            "name": s.name,
            "provider": "managed",
            "region": "unknown",
            "status": "healthy" if vm.percent < 80 else "warning",
            "public_ip": s.host,
            "cpu": {"cores": psutil.cpu_count() or 0, "load_average": [round(load[0], 2), round(load[1], 2), round(load[2], 2)]},
            "memory": {"total_mb": round(vm.total / (1024 ** 2), 2), "used_mb": round(vm.used / (1024 ** 2), 2), "free_mb": round(vm.available / (1024 ** 2), 2), "usage_percent": round(vm.percent, 2)},
            "disk": {"total_gb": round(disk.total / (1024 ** 3), 2), "used_gb": round(disk.used / (1024 ** 3), 2), "free_gb": round(disk.free / (1024 ** 3), 2), "usage_percent": round((disk.used / max(1, disk.total)) * 100, 2)},
            "containers": {"running": running, "failed": failed, "building": services.filter(deployments__status__in=[Deployment.Status.BUILDING, Deployment.Status.DEPLOYING]).distinct().count()},
            "uptime_seconds": int(timezone.now().timestamp() - psutil.boot_time()),
            "warnings": ["High memory pressure"] if vm.percent >= 85 else [],
        } for s in servers] or [{
            "id": "local-node",
            "name": socket.gethostname(),
            "provider": "local",
            "region": "unknown",
            "status": "healthy" if vm.percent < 80 else "warning",
            "cpu": {"cores": psutil.cpu_count() or 0, "load_average": [round(load[0], 2), round(load[1], 2), round(load[2], 2)]},
            "memory": {"total_mb": round(vm.total / (1024 ** 2), 2), "used_mb": round(vm.used / (1024 ** 2), 2), "free_mb": round(vm.available / (1024 ** 2), 2), "usage_percent": round(vm.percent, 2)},
            "disk": {"total_gb": round(disk.total / (1024 ** 3), 2), "used_gb": round(disk.used / (1024 ** 3), 2), "free_gb": round(disk.free / (1024 ** 3), 2), "usage_percent": round((disk.used / max(1, disk.total)) * 100, 2)},
            "containers": {"running": running, "failed": failed, "building": services.filter(deployments__status__in=[Deployment.Status.BUILDING, Deployment.Status.DEPLOYING]).distinct().count()},
            "uptime_seconds": int(timezone.now().timestamp() - psutil.boot_time()),
            "warnings": [],
        }]
        return Response({"nodes": nodes, "summary": {"total_nodes": len(nodes), "healthy_nodes": sum(1 for n in nodes if n["status"] == "healthy"), "critical_nodes": 0, "total_ram_mb": sum(n["memory"]["total_mb"] for n in nodes), "used_ram_mb": sum(n["memory"]["used_mb"] for n in nodes), "total_disk_gb": sum(n["disk"]["total_gb"] for n in nodes), "used_disk_gb": sum(n["disk"]["used_gb"] for n in nodes)}})


class RemoteTriggerView(GenericAPIView):
    """
    Direct endpoint for node-to-node deployment triggers.
    Authenticated via ZeroTrustHMACAuthentication.
    """
    authentication_classes = [ZeroTrustHMACAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        from .serializers import DeploymentTriggerSerializer
        serializer = DeploymentTriggerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        service_id = serializer.validated_data['service_id']
        provider_id = serializer.validated_data['provider_id']
        skip_review = serializer.validated_data.get('skip_review', False)
        ref = serializer.validated_data.get('commit_hash', 'HEAD')
        source_node = request.data.get('source_node', 'remote-controller')

        try:
            service = Service.objects.get(id=service_id)
            # Determine provider (or use the one passed in if it belongs to this node)
            from apps.cloud.models import CloudProvider
            provider = CloudProvider.objects.filter(id=provider_id).first()
            if not provider:
                # Fallback to resolving local provider
                from .tasks import _resolve_provider_for_service
                provider = _resolve_provider_for_service(service)

            if not provider:
                return Response({"error": "No valid cloud provider found on this node"}, status=400)

            # Create deployment
            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash=ref if ref != 'HEAD' else 'latest',
                commit_message=f"Remote Trigger: {ref} (via {source_node})",
                source_node=source_node
            )

            # Enqueue task
            enqueue_smart_deploy_task(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
                skip_review=skip_review
            )

            return Response(DeploymentSerializer(deployment).data, status=status.HTTP_201_CREATED)

        except Service.DoesNotExist:
            return Response({"error": "Service not found on this node"}, status=404)
        except Exception as e:
            logger.exception("Remote trigger failed")
            return Response({"error": str(e)}, status=500)



from apps.deployments.models_registry import RegistryCredential
from apps.deployments.serializers import RegistryCredentialSerializer


class RegistryCredentialViewSet(viewsets.ModelViewSet):
    serializer_class = RegistryCredentialSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RegistryCredential.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=['post'])
    def test_connection(self, request, pk=None):
        credential = self.get_object()
        try:
            import docker
            client = docker.from_env()
            result = client.login(
                username=credential.username,
                password=credential.password,
                registry=credential.registry_url,
            )
            return Response({'status': 'success', 'message': result.get('Status', 'Login succeeded')})
        except Exception:
            logger.exception("Registry connection test failed")
            return Response({'status': 'error', 'message': 'Connection test failed. Please verify your credentials.'}, status=400)


class SecurityStatusView(GenericAPIView):
    """
    Return live system security & hardening status.

    GET /api/v1/system/security-status/

    Reports the status of all active security layers:
      - Container isolation (gVisor / Kata / runc)
      - Mandatory access control (AppArmor, seccomp)
      - Runtime protection (no-new-privileges, capability drops)
      - Threat detection (Falco, CrowdSec, auditd)
      - Network security (UFW, fail2ban)
      - Vulnerability management (Trivy)
      - Kernel hardening (sysctl)
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from apps.deployments.models_core import PlatformConfig
        from apps.deployments.services.container_runtime import (
            _kata_available,
            _runsc_available,
            detect_best_runtime,
            is_sandboxed_runtime,
        )

        config = PlatformConfig.load()
        runtime = detect_best_runtime()

        # ── Container runtime ──────────────────────────────────────
        isolation_model = "process-level (runc)"
        if runtime == "runsc":
            isolation_model = "user-space kernel (gVisor)"
        elif runtime == "kata-runtime":
            isolation_model = "VM-level (Kata)"

        container_runtime = {
            "active": runtime,
            "sandboxed": is_sandboxed_runtime(runtime),
            "isolation_model": isolation_model,
            "kata_available": _kata_available(),
            "gvisor_available": _runsc_available(),
        }

        # ── AppArmor ────────────────────────────────────────────────
        apparmor = {"enabled": False, "profiles_loaded": 0}
        try:
            import subprocess
            result = subprocess.run(
                ["aa-status", "--enabled"],
                capture_output=True, text=True, timeout=5,
            )
            apparmor["enabled"] = result.returncode == 0
            if apparmor["enabled"]:
                count_result = subprocess.run(
                    ["aa-status", "--profiled"],
                    capture_output=True, text=True, timeout=5,
                )
                try:
                    apparmor["profiles_loaded"] = int(
                        (count_result.stdout or "").strip()
                    )
                except (ValueError, TypeError):
                    apparmor["profiles_loaded"] = -1
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            apparmor["enabled"] = False

        # ── seccomp ─────────────────────────────────────────────────
        seccomp = {"enabled": False}
        try:
            seccomp_result = subprocess.run(
                ["docker", "info", "--format", "{{json .SecurityOptions}}"],
                capture_output=True, text=True, timeout=10,
            )
            seccomp["enabled"] = "seccomp" in (seccomp_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            seccomp["enabled"] = False

        # ── Falco ───────────────────────────────────────────────────
        falco = {"running": False, "container": "smsly-falco", "driver": "unknown", "events_detected": 0}
        try:
            ps_result = subprocess.run(
                ["docker", "ps", "--filter", f"name={falco['container']}",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=10,
            )
            falco["running"] = "Up" in (ps_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            falco["running"] = False
        if falco["running"]:
            try:
                driver_result = subprocess.run(
                    ["docker", "exec", falco["container"],
                     "falco", "--list-options"],
                    capture_output=True, text=True, timeout=10,
                )
                if "modern_ebpf" in (driver_result.stdout or ""):
                    falco["driver"] = "modern_ebpf"
                elif "ebpf" in (driver_result.stdout or "").lower():
                    falco["driver"] = "ebpf"
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            try:
                subprocess.run(
                    ["docker", "exec", falco["container"],
                     "falcosidekick", "--version"],
                    capture_output=True, text=True, timeout=5,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass

        # ── CrowdSec ────────────────────────────────────────────────
        crowdsec = {
            "enabled": config.enable_crowdsec_waf,
            "running": False,
            "container": "smsly-crowdsec",
        }
        if crowdsec["enabled"]:
            try:
                ps_result = subprocess.run(
                    ["docker", "ps", "--filter", f"name={crowdsec['container']}",
                     "--format", "{{.Status}}"],
                    capture_output=True, text=True, timeout=10,
                )
                crowdsec["running"] = "Up" in (ps_result.stdout or "")
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                crowdsec["running"] = False
            # Fetch active ban decisions for visibility
            if crowdsec["running"]:
                try:
                    bans_result = subprocess.run(
                        ["docker", "exec", crowdsec["container"],
                         "cscli", "decisions", "list", "-o", "json"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if bans_result.returncode == 0:
                        import json
                        bans = json.loads(bans_result.stdout)
                        crowdsec["active_bans"] = len(bans) if isinstance(bans, list) else 0
                    else:
                        crowdsec["active_bans"] = -1
                except (FileNotFoundError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
                    crowdsec["active_bans"] = -1
            else:
                crowdsec["active_bans"] = 0

        # ── UFW ─────────────────────────────────────────────────────
        ufw = {"active": False}
        try:
            ufw_result = subprocess.run(
                ["ufw", "status"],
                capture_output=True, text=True, timeout=5,
            )
            ufw["active"] = "Status: active" in (ufw_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            ufw["active"] = False

        # ── fail2ban ────────────────────────────────────────────────
        fail2ban = {"active": False, "jails": []}
        try:
            f2b_result = subprocess.run(
                ["fail2ban-client", "ping"],
                capture_output=True, text=True, timeout=5,
            )
            fail2ban["active"] = "pong" in (f2b_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            fail2ban["active"] = False
        if fail2ban["active"]:
            try:
                jails_result = subprocess.run(
                    ["fail2ban-client", "status"],
                    capture_output=True, text=True, timeout=5,
                )
                for line in (jails_result.stdout or "").splitlines():
                    if line.strip().startswith("Jail list:"):
                        jails_str = line.split(":", 1)[1].strip()
                        fail2ban["jails"] = [j.strip() for j in jails_str.split(",") if j.strip()]
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass

        # ── auditd ──────────────────────────────────────────────────
        auditd = {"active": False}
        try:
            audit_result = subprocess.run(
                ["systemctl", "is-active", "auditd"],
                capture_output=True, text=True, timeout=5,
            )
            auditd["active"] = (audit_result.stdout or "").strip() == "active"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            auditd["active"] = False

        # ── Docker socket proxy ─────────────────────────────────────
        socket_proxy = {"enabled": False}
        try:
            sp_result = subprocess.run(
                ["docker", "ps", "--filter", "name=socket-proxy",
                 "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=10,
            )
            socket_proxy["enabled"] = "Up" in (sp_result.stdout or "")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            socket_proxy["enabled"] = False

        # ── Trivy ───────────────────────────────────────────────────
        trivy = {
            "enabled": config.trivy_enabled,
            "fail_on_severity": config.trivy_fail_on_severity,
            "installed": False,
        }
        try:
            from apps.deployments.utils import find_binary
            trivy_bin = find_binary("trivy")
            if trivy_bin:
                trivy_result = subprocess.run(
                    [trivy_bin, "--version"],
                    capture_output=True, text=True, timeout=5,
                )
                trivy["installed"] = trivy_result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ImportError):
            trivy["installed"] = False

        # ── Kernel hardening ────────────────────────────────────────
        kernel = {"enabled": False}
        try:
            kptr = subprocess.run(
                ["sysctl", "-n", "kernel.kptr_restrict"],
                capture_output=True, text=True, timeout=5,
            )
            kernel["enabled"] = (kptr.stdout or "").strip() == "2"
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            kernel["enabled"] = False

        # ── no-new-privileges (system-level) ────────────────────────
        no_new_privs = {"enabled": True}  # enforced per-container via security_opt

        # ── Device Trust (Beta) ────────────────────────────────────
        device_trust = {
            "enabled": config.enforce_device_trust,
            "beta": True,
            "registered_devices": 0,
        }
        try:
            from apps.deployments.models_core import TrustedDevice
            device_trust["registered_devices"] = TrustedDevice.objects.filter(
                is_active=True
            ).count()
        except Exception:
            pass

        return Response({
            "container_runtime": container_runtime,
            "apparmor": apparmor,
            "seccomp": seccomp,
            "no_new_privileges": no_new_privs,
            "falco": falco,
            "crowdsec": crowdsec,
            "ufw": ufw,
            "fail2ban": fail2ban,
            "auditd": auditd,
            "docker_socket_proxy": socket_proxy,
            "trivy": trivy,
            "device_trust": device_trust,
            "kernel_hardening": kernel,
        })


class PlatformConfigViewSet(viewsets.GenericViewSet):
    """
    ViewSet for platform-wide configurations and Infisical secret synchronization.
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        from apps.deployments.models_core import PlatformConfig
        return PlatformConfig.objects.all()

    @action(detail=False, methods=["post"], url_path="sync-infisical")
    def sync_infisical(self, request):
        from apps.deployments.services.infisical import (
            get_infisical_client,
            get_or_create_workspace,
            pull_platform_config_from_infisical,
            push_platform_config_to_infisical,
        )
        client = get_infisical_client()
        if not client:
            return Response(
                {"status": "error", "message": "Infisical client not configured or unreachable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        ws_id = get_or_create_workspace(client)
        if not ws_id:
            return Response(
                {"status": "error", "message": "Failed to resolve Infisical workspace."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        pull_res = pull_platform_config_from_infisical(client, ws_id)
        push_res = push_platform_config_to_infisical(client, ws_id)

        pushed = push_res.get("synced", [])
        pulled = pull_res.get("synced", [])
        failed = push_res.get("failed", []) + pull_res.get("failed", [])

        return Response({
            "status": "success" if not failed else "partial",
            "synced_count": len(pushed) + len(pulled),
            "pushed": pushed,
            "pulled": pulled,
            "failed": failed,
        })

