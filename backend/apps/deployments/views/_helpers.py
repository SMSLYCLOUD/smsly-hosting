"""Shared helper functions for views."""

import contextlib
import hmac
import logging
import os
import re
import uuid

from django.conf import settings
from django.core import signing
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header
from rest_framework import authentication, permissions, serializers, status
from rest_framework.response import Response

from apps.cloud.models import CloudProvider
from apps.domains.utils import normalize_domain
from apps.deployments.models import Deployment, Service
from apps.deployments.models.audit import AuditLog

logger = logging.getLogger(__name__)


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
        from .upload_security import MAX_KEY_FILE_SIZE
        if key_file.size > MAX_KEY_FILE_SIZE:
            return None
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
        from ..models.core import PlatformConfig
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
            from ..models.core import PlatformConfig
            cfg = PlatformConfig.load()
            db_secret = str(getattr(cfg, 'caddy_ask_secret', '') or '').strip()
            if db_secret:
                return db_secret
        except Exception as exc:
            logger.debug("Failed to load Caddy ask secret from PlatformConfig: %s", exc)
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
        from apps.cloud.services.github_webhooks import setup_github_webhook
        setup_github_webhook(user, repo_url)
    elif 'gitlab' in hostname:
        from apps.cloud.services.gitlab_webhooks import setup_gitlab_webhook
        setup_gitlab_webhook(user, repo_url)
    elif 'bitbucket' in hostname:
        from apps.cloud.services.bitbucket_webhooks import setup_bitbucket_webhook
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
    except Exception as exc:
        logger.debug("Custom domain lookup failed: %s", exc)
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
    from ..models.core import ManagedServer

    # Determine the local IP (the one the host is reachable on)
    try:
        host = socket.gethostbyname(socket.gethostname())
    except Exception:
        host = "127.0.0.1"

    # Load platform config for the domain
    try:
        from apps.deployments.models.core import PlatformConfig
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

    from apps.deployments.models.core import ManagedServer

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


__all__ = [
    '_resolve_encryption_key',
    'ZeroTrustHMACAuthentication',
    '_check_tier_gates_disabled',
    'MAINTENANCE_ACTIONS',
    '_error_response',
    'CleanupFileResponse',
    '_backup_download_headers',
    '_verify_signed_download',
    '_generate_signed_download_url',
    '_parse_single_range',
    '_file_iterator',
    '_open_backup_download_response',
    'EmptySerializer',
    'CaddySecretOrAdminPermission',
    '_cancel_stale_in_progress_deployments',
    '_setup_provider_webhook',
    '_has_active_deployment',
    '_resolve_provider_for_service',
    '_normalize_request_domain',
    '_rewrite_public_domain',
    '_service_for_domain',
    '_parse_bool',
    '_is_local_deploy_target',
    '_ensure_local_server_record',
    '_resolve_local_provider',
    '_resolve_provider_for_target',
    '_resolve_requested_deploy_target',
    '_is_valid_env_key',
    '_looks_masked_secret',
    'is_remote_sync_request',
]

