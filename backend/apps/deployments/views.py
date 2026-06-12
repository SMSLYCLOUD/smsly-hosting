# pylint: disable=invalid-name
# pylint: disable=too-many-lines
"""Views module."""
import os
import posixpath
import hmac
from rest_framework import viewsets, permissions, status, parsers, serializers, authentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.generics import GenericAPIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.utils import timezone
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.db.models import Prefetch
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField
from django.utils.http import content_disposition_header
from django.core import signing
from apps.deployments.services.github_webhooks import setup_github_webhook
import threading
from .ai_router import (
    DEFAULT_AI_ROUTER_API_BASE,
    DEFAULT_AI_ROUTER_UI_BASE,
    DEFAULT_BRAID_ALIAS,
    is_ai_router_service,
    persist_ai_router_config,
    serialize_ai_router_config,
)
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .serializers import (
    ServiceSerializer, DeploymentSerializer,
    DeploymentTriggerSerializer, EnvVarSerializer,
    DeploymentTimelineSerializer, InstantRollbackSerializer,
    AuditLogSerializer, DeploymentApproveSerializer,
    ServiceBackupSerializer, ServerBackupSerializer, BackupScheduleSerializer
)
from .models_audit import AuditLog
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .tasks import (
    smart_deploy_task,
    resume_deploy_task,
    create_service_backup_task,
    create_server_backup_task,
    restore_service_backup_task,
    enqueue_smart_deploy_task,
)
from .rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from .domain_utils import normalize_domain
from .services.server_guard import ServerGuard
from apps.cloud.models import CloudProvider
import uuid
import logging
import re
from celery.result import AsyncResult
from apps.cloud.docker_client import get_docker_client
from .utils import validate_and_sanitize_path as _validate_and_sanitize_path
from apps.deployments.utils import resolve_running_container


class ZeroTrustHMACAuthentication(authentication.BaseAuthentication):
    """
    Authenticate requests from peer nodes using HMAC V2.
    Required headers: X-Gateway-Signature-V2, X-Request-Timestamp
    """
    def authenticate(self, request):
        import hashlib
        import hmac
        import time
        from django.contrib.auth import get_user_model
        User = get_user_model()

        signature = request.headers.get("X-Gateway-Signature-V2", "")
        timestamp = request.headers.get("X-Request-Timestamp", "")
        if not signature or not timestamp:
            return None

        # Verify timestamp freshness (1 min window)
        try:
            req_ts = int(timestamp)
            if abs(int(time.time()) - req_ts) > 60:
                raise authentication.AuthenticationFailed("Timestamp expired")
        except ValueError:
            raise authentication.AuthenticationFailed("Invalid timestamp")

        # Verify HMAC
        gw_secret = getattr(settings, "GATEWAY_SECRET", settings.SECRET_KEY)
        method = request.method
        path = request.get_full_path()
        
        # For remote triggers, we need to handle request body carefully
        try:
            body = request.body
        except Exception:
            body = b""
            
        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"{method}|{path}|{timestamp}|{body_hash}"
        expected = hmac.new(gw_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise authentication.AuthenticationFailed("Invalid HMAC signature")

        # Authentication success — use the first active superuser as the actor
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            raise authentication.AuthenticationFailed("No admin user available")

        return (admin, None)

logger = logging.getLogger(__name__)

MAINTENANCE_ACTIONS = {
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
            try:
                os.remove(self._file_path)
            except OSError:
                pass
        if self._file_path:
            parent = os.path.dirname(os.path.abspath(self._file_path))
            if parent and os.path.basename(parent).startswith('smsly-decrypted-'):
                try:
                    os.rmdir(parent)
                except OSError:
                    pass


_BACKUP_DOWNLOAD_BLOCK_SIZE = 1024 * 1024
_BACKUP_DOWNLOAD_CONTENT_TYPE = "application/gzip"


def _backup_download_headers(response, file_size: int, filename: str):
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
    payload = {'pk': str(obj_pk), 'ts': int(time.time())}
    signed = signing.TimestampSigner().sign_object(payload)
    from urllib.parse import urlencode
    params = {'signed': signed}
    if path_params:
        params.update(path_params)
    return request.build_absolute_uri(f"/api/v1/{url_name}/?{urlencode(params)}")


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
            try:
                os.remove(cleanup_path)
            except OSError:
                pass
        if cleanup_path:
            parent = os.path.dirname(os.path.abspath(cleanup_path))
            if parent and os.path.basename(parent).startswith('smsly-decrypted-'):
                try:
                    os.rmdir(parent)
                except OSError:
                    pass


def _open_backup_download_response(request, file_path: str, filename: str, cleanup_path: str | None = None):
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get('Range') or request.META.get('HTTP_RANGE')
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

    * the request carries ``X-Caddy-Secret`` matching ``settings.CADDY_ASK_SECRET``
      (machine-to-machine Caddy), OR
    * the request is from an authenticated admin user (human operator inspecting
      the endpoint).

    All other requests are denied with HTTP 401.
    """

    message = "Caddy ask endpoint requires a valid X-Caddy-Secret header or admin authentication."

    def has_permission(self, request, view):
        provided = request.headers.get("X-Caddy-Secret", "")
        expected = str(getattr(settings, "CADDY_ASK_SECRET", "") or "")
        if expected and provided and hmac.compare_digest(provided, expected):
            return True
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False) and (
            getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)
        ):
            return True
        return False


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


def _has_active_deployment(service):
    """
    Check if a service already has an active deployment in progress.
    Returns the existing deployment if found, None otherwise.
    Prevents rapid-fire deployment spam from the dashboard.
    """
    _cancel_stale_in_progress_deployments(service)
    return service.deployments.filter(
        status__in=_IN_PROGRESS_DEPLOYMENT_STATUSES
    ).order_by('-created_at').first()


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

    for service in Service.objects.only("id", "custom_domains"):
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
        return {
            "ok": True,
            "specified": False,
            "target_server": None,
            "target_is_local": False,
            "effective_server": getattr(service, 'server', None),
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


class ServiceViewSet(viewsets.ModelViewSet):
    """
    Service Management and Nested Resources.
    """
    queryset = Service.objects.all().order_by('-updated_at')
    serializer_class = ServiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [BurstRateThrottle, DeploymentRateThrottle]

    def get_queryset(self):
        """ZH-001 FIX: Return services owned by user. APITokens (remote proxies) skip owner check."""
        qs = self.queryset.prefetch_related('deployments')
        # hasattr(self.request.auth, 'prefix') means this is an APIToken from another server
        if self.request.user.is_superuser or hasattr(self.request.auth, 'prefix'):
            return qs.all().order_by('-created_at')
        return qs.filter(
            Q(owner=self.request.user) |
            Q(project__team__members__user=self.request.user)
        ).distinct().order_by('-created_at')

    def _is_remote_sync_request(self):
        logger.debug("Checking remote sync request...")
        token = getattr(self.request, 'auth', None)
        authenticator = getattr(self.request, 'successful_authenticator', None)
        authenticator_name = authenticator.__class__.__name__ if authenticator else ''
        is_hmac_remote_sync = authenticator_name == 'RemoteSyncHMACAuthentication'
        
        # The token could be named 'node:Node-IP' or 'Primary-admin' (from installer).
        # Since the token is a secure APIToken (hasattr prefix), and it's sending
        # the X-SMSLY-Remote-Sync header, we can trust it.
        is_api_token = hasattr(token, 'prefix')
        
        has_header = self.request.headers.get('X-SMSLY-Remote-Sync') == '1'
        return has_header and (is_api_token or is_hmac_remote_sync)

    def perform_create(self, serializer):
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
            if server:
                logger.info("Auto-assigning server %s to service %s", server.name, serializer.validated_data.get('name'))
        
        ServerGuard.assert_user_workload_allowed(server)

        deploy_type = serializer.validated_data.get('deploy_type', 'GIT')

        service = serializer.save(owner=self.request.user, server=server)

        # Setup GitHub Webhook only for direct user actions. Node-to-node
        # remote sync uses APIToken auth and should not mutate repo webhooks.
        if (
            not self._is_remote_sync_request()
            and service.deploy_type == 'GIT'
            and service.repository_url
        ):
            threading.Thread(
                target=setup_github_webhook,
                args=(self.request.user, service.repository_url),
                daemon=True
            ).start()

    def perform_update(self, serializer):
        from .models_core import ManagedServer
        
        old_repo_url = serializer.instance.repository_url if serializer.instance else None
        
        if 'server' in serializer.validated_data:
            server = serializer.validated_data.get('server')
            
            # Seamless: If no server is assigned during update, default to primary
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

        # Setup GitHub Webhook if repo URL changed or was newly set
        new_repo_url = service.repository_url
        if (
            not self._is_remote_sync_request()
            and service.deploy_type == 'GIT'
            and new_repo_url
            and new_repo_url != old_repo_url
        ):
            threading.Thread(
                target=setup_github_webhook,
                args=(self.request.user, new_repo_url),
                daemon=True
            ).start()


    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
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
        from .tasks import delete_service_task
        from .models_core import Service

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

        try:
            from apps.deployments.utils_target import resolve_active_execution_target
            target = resolve_active_execution_target(service)
            active_server = target["server_obj"]

            if target["target_type"] in ("remote", "lite_agent") and active_server:
                from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
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
        service = self.get_object()
        force_rebuild = _parse_bool(request.data.get('force_rebuild', False))

        # Clear health monitor restart state (ends exponential backoff)
        from apps.deployments.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))

        # ── Fast restart path: just docker restart the container ──
        if not force_rebuild:
            try:
                from apps.deployments.utils_target import resolve_active_execution_target
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
                    from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
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

        # ── Full rebuild path ──
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
            from apps.cloud.docker_client import get_docker_client
            import docker as docker_lib

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
        branch = request.data.get('branch')
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

                # Copy env vars from parent (skip any already created by signals e.g. SMSLY_API_KEY)
                for env in parent.env_vars.all():
                    EnvironmentVariable.objects.get_or_create(
                        service=preview,
                        key=env.key,
                        defaults={
                            'value': env.value,
                            'is_secret': env.is_secret,
                            'source': env.source,
                        }
                    )

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
                {'error': f'Failed to create preview: {str(exc)}'},
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
        ).order_by('-created_at')

        data = []
        for preview in previews:
            latest_deploy = preview.deployments.order_by('-created_at').first()
            data.append({
                'id': str(preview.id),
                'name': preview.name,
                'branch': preview.branch,
                'pr_number': preview.pr_number,
                'preview_url': preview.service_url,
                'health_status': preview.health_status,
                'created_at': preview.created_at.isoformat(),
                'latest_deployment': {
                    'id': str(latest_deploy.id),
                    'status': latest_deploy.status,
                    'created_at': latest_deploy.created_at.isoformat(),
                } if latest_deploy else None,
            })

        return Response({'count': len(data), 'results': data})

    @action(detail=True, methods=['delete'], url_path='destroy-preview')
    def destroy_preview(self, request, pk=None):
        """
        Destroy a preview environment.
        DELETE /api/v1/services/{id}/destroy-preview/
        Body: { "preview_id": "uuid" }
        """
        parent = self.get_object()
        preview_id = request.data.get('preview_id')

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
        import ipaddress
        from urllib.parse import urlparse
        import requests as req_lib
        from .models_servers import ManagedServer

        service = self.get_object()
        ref = str(request.data.get('ref', 'HEAD'))[:200]
        server_ids = request.data.get('server_ids', [])
        include_local = request.data.get('include_local', True)

        from .models_core import PlatformConfig
        p_config = PlatformConfig.objects.first()
        local_node_id = p_config.server_ip if p_config else "Controller"

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
        Body: { "message": "optional reason" } (Optional)

        This is the ONE-CLICK rollback that beats Railway.
        No need to find the deployment ID — just hit this endpoint.
        """
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
        if provider:
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

    @action(detail=True, methods=['delete', 'patch'],
            url_path='env_vars/(?P<var_id>\\d+)')
    def delete_env_var(self, request, pk=None, var_id=None):
        service = self.get_object()
        try:
            var = EnvironmentVariable.objects.get(id=var_id, service=service)
        except EnvironmentVariable.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

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

    def get_throttles(self):
        """Throttle the Caddy ask endpoint to limit Let's Encrypt blast radius."""
        if self.action == 'check_domain':
            from rest_framework.throttling import ScopedRateThrottle
            throttle = ScopedRateThrottle()
            throttle.scope = 'caddy_ask'
            self.throttle_scope = 'caddy_ask'
            return [throttle]
        return super().get_throttles()

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
        raw_domain_for_cap = request.query_params.get('domain', '').strip().lower()
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
            service = Service.objects.get(id=pk)
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
            # Resolve token to a Service if possible
            try:
                dep_svc = Service.objects.filter(name__iexact=token).first()
                if dep_svc:
                    deps.append({"id": str(dep_svc.id), "name": dep_svc.name})
            except Exception:
                continue

        # Find dependents (services that list this one in their depends_on)
        dependents_qs = Service.objects.filter(plan__contains={"depends_on": [service.name]})
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

        services_qs = Service.objects.filter(id__in=ids)
        results = []
        for svc in services_qs:
            try:
                if action == 'deploy':
                    # Queue a smart_deploy_task for each service
                    from apps.deployments.tasks import smart_deploy_task
                    smart_deploy_task.delay(str(svc.id))
                elif action == 'cancel':
                    # Cancel any queued or building deployments
                    from apps.deployments.models import Deployment
                    Deployment.objects.filter(service=svc, status__in=[Deployment.Status.QUEUED, Deployment.Status.BUILDING]).update(status=Deployment.Status.CANCELLED)
                elif action == 'senate':
                    # Trigger AI Senate env enrichment (re‑use existing logic)
                    from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService
                    env_context = {}  # placeholder – real implementation would gather context
                    suggestions = EnvironmentIntelligenceService.resolve_environment(env_context, svc.stack or '', svc.name)
                    from apps.deployments.models import EnvironmentVariable
                    import re
                    for k, v in suggestions.items():
                        if not re.match(r'^[A-Za-z0-9_][A-Za-z0-9_.-]*$', k):
                            logger.warning("Skipping invalid env var key from Senate: %s", k)
                            continue
                        EnvironmentVariable.objects.update_or_create(service=svc, key=k, defaults={'value': v, 'is_secret': False})
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
        for svc in Service.objects.all():
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
            from services.caddy_manager import generate_caddyfile, apply_caddyfile
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
        from django.conf import settings
        if getattr(settings, 'SMSLY_DISABLE_TIER_GATES', False):
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
                retry (callable(resp, orchestrator, remote_id, config)->Response|None, optional),
                k8s_handler (callable(container_id, path)->Response, optional),
                k8s_command (list, optional).
            local_action: callable(container, path=None) -> Response.
            path: Optional path string for symlink resolution and K8s command.

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
            container_id = (latest_deploy.container_id or "")
            if container_id.startswith('k8s://'):
                k8s_handler = remote_config.get('k8s_handler')
                if k8s_handler:
                    return k8s_handler(container_id, path)
                if path is not None:
                    k8s_command = remote_config.get('k8s_command')
                    if k8s_command:
                        return self._k8s_exec_file_op(container_id, k8s_command)
                return Response({'error': 'K8s operation not supported'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            return Response({'error': 'No running container found'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Symlink resolution for Docker containers
        if path is not None:
            try:
                path = _validate_and_sanitize_path(path, container=container)
            except Exception:
                pass

        return local_action(container, path) if path is not None else local_action(container)


    @action(detail=True, methods=['get'], url_path='file-browse')
    def file_browse(self, request, pk=None):
        """List files inside the running container (Docker, K8s, or remote node)."""
        service = self.get_object()
        path = request.query_params.get('path', '/')

        try:
            path = _validate_and_sanitize_path(path)
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
            """Retry file_browse with fallback path (/app <-> /)."""
            original_path = config.get('params', {}).get('path', '')
            if original_path not in ('/app', '/'):
                return None
            fallback_path = '/' if original_path == '/app' else '/app'
            logger.warning(
                f"Remote file_browse failed for path {original_path}, "
                f"trying fallback: {fallback_path}. "
                f"Error: {resp.status_code if resp else 'Timeout'}"
            )
            try:
                fallback_resp = orchestrator._request(
                    method='GET',
                    path=f"/api/v1/services/{remote_id}/file-browse/",
                    params={'path': fallback_path},
                    timeout=10,
                )
                if fallback_resp and fallback_resp.status_code == 200:
                    data = fallback_resp.json()
                    data['path'] = fallback_path
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
                'k8s_handler': lambda cid, p: self._k8s_file_browse(cid, p),
            },
            local_action=lambda container, path=None: self._exec_file_list(container, path or '/'),
            path=path,
        )

    def _k8s_file_browse(self, container_id: str, path: str):
        """List files via K8s exec into the pod."""
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except BaseException:
                k8s_config.load_kube_config()
            parts = container_id.replace('k8s://', '').split('/', 1)
            namespace = parts[0] if len(parts) > 1 else 'default'
            pod_name = parts[-1]
            core_v1 = k8s_client.CoreV1Api()

            cmd_chain = [
                ['ls', '-la', '--time-style=long-iso', path],
                ['ls', '-la', path],
                ['python3', '-c', (
                    "import os,stat,datetime,sys\n"
                    "p=sys.argv[1]\n"
                    "for f in os.listdir(p):\n"
                    " fp=os.path.join(p,f)\n"
                    " s=os.lstat(fp)\n"
                    " mt=datetime.datetime.fromtimestamp(s.st_mtime).strftime('%Y-%m-%d %H:%M')\n"
                    " print(stat.filemode(s.st_mode),s.st_nlink,s.st_uid,s.st_gid,s.st_size,mt,f)"
                ), path],
            ]
            output = ""
            last_err = ""
            for cmd in cmd_chain:
                try:
                    resp = core_v1.connect_get_namespaced_pod_exec(
                        pod_name, namespace,
                        command=cmd,
                        stderr=True, stdin=False,
                        stdout=True, tty=False,
                        _request_timeout=30,
                    )
                    output = resp
                    if isinstance(output, bytes):
                        output = output.decode('utf-8', errors='replace')
                    if not any(err in output for err in (
                        'unrecognized option', 'invalid option',
                        'No such file', 'cannot access',
                        'No such file or directory', 'command not found',
                        'executable file not found',
                    )):
                        break
                    last_err = output
                    output = ""
                except Exception:
                    continue

            if not output or 'No such file' in output or 'cannot access' in output or 'No such file or directory' in output:
                fallback_path = '/' if path == '/app' else ('/app' if path == '/' else None)
                if fallback_path:
                    path = fallback_path
                    for cmd in cmd_chain:
                        try:
                            resp = core_v1.connect_get_namespaced_pod_exec(
                                pod_name, namespace,
                                command=cmd,
                                stderr=True, stdin=False,
                                stdout=True, tty=False,
                                _request_timeout=30,
                            )
                            output = resp
                            if isinstance(output, bytes):
                                output = output.decode('utf-8', errors='replace')
                            if not any(err in output for err in (
                                'unrecognized option', 'invalid option',
                                'No such file', 'cannot access',
                                'No such file or directory', 'command not found',
                                'executable file not found',
                            )):
                                break
                            last_err = output
                            output = ""
                        except Exception:
                            continue

            if not output:
                return Response(
                    {'error': 'Failed to list directory', 'details': last_err or output},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            files = self._parse_ls_output(output)
            return Response({'path': path, 'files': files})
        except ImportError:
            return Response({'error': 'Kubernetes client not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _k8s_exec_file_op(self, container_id: str, command_args: list):
        """Execute a file operation command inside a K8s pod."""
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except BaseException:
                k8s_config.load_kube_config()
            parts = container_id.replace('k8s://', '').split('/', 1)
            namespace = parts[0] if len(parts) > 1 else 'default'
            pod_name = parts[-1]
            core_v1 = k8s_client.CoreV1Api()
            resp = core_v1.connect_get_namespaced_pod_exec(
                pod_name, namespace,
                command=command_args,
                stderr=True, stdin=False,
                stdout=True, tty=False,
                _request_timeout=30,
            )
            output = resp
            if isinstance(output, bytes):
                output = output.decode('utf-8', errors='replace')
            if 'No such file' in output or 'cannot access' in output:
                return Response({'error': 'Path not found', 'details': output}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'output': output})
        except ImportError:
            return Response({'error': 'Kubernetes client not available'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _exec_file_list(self, container, path: str):
        """List files via Docker exec with fallback chain for containers missing coreutils."""
        try:
            cmd_chain = [
                ["ls", "-la", "--time-style=long-iso", path],
                ["ls", "-la", path],
                # Python-based fallback for distroless/minimal images without ls
                ["python3", "-c", (
                    "import os,stat,datetime,sys\n"
                    "p=sys.argv[1]\n"
                    "for f in os.listdir(p):\n"
                    " fp=os.path.join(p,f)\n"
                    " s=os.lstat(fp)\n"
                    " mt=datetime.datetime.fromtimestamp(s.st_mtime).strftime('%Y-%m-%d %H:%M')\n"
                    " print(stat.filemode(s.st_mode),s.st_nlink,s.st_uid,s.st_gid,s.st_size,mt,f)"
                ), path],
            ]
            exit_code = 1
            output = b""
            for cmd in cmd_chain:
                exit_code, output = container.exec_run(cmd)
                if exit_code == 0:
                    break

            if exit_code != 0:
                fallback_path = '/' if path == '/app' else ('/app' if path == '/' else None)
                if fallback_path:
                    path = fallback_path
                    for cmd in cmd_chain:
                        exit_code, output = container.exec_run(cmd)
                        if exit_code == 0:
                            break

            if exit_code != 0:
                logger.warning("_exec_file_list 400: ls command failed. Code: %s, Output: %s", exit_code, output.decode('utf-8', errors='replace'))
                return Response({'error': 'Failed to list directory', 'details': output.decode('utf-8', errors='replace')}, status=status.HTTP_400_BAD_REQUEST)
            files = self._parse_ls_output(output.decode('utf-8', errors='replace'))
            return Response({'path': path, 'files': files})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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

    def _parse_ls_output(self, output: str) -> list:
        """Parse `ls -la` output into file dicts. Supports standard and long-iso time styles."""
        import re
        files = []
        lines = output.splitlines()
        if lines and lines[0].startswith('total'):
            lines = lines[1:]
        for line in lines:
            parts = line.split()
            if not parts:
                continue
                
            # Detect if time-style=long-iso (e.g. 2026-05-24)
            if len(parts) >= 8 and re.match(r'\d{4}-\d{2}-\d{2}', parts[5]):
                date = f"{parts[5]} {parts[6]}"
                name = " ".join(parts[7:])
            elif len(parts) >= 9:
                # Standard ls -la output: Month Day Time
                date = f"{parts[5]} {parts[6]} {parts[7]}"
                name = " ".join(parts[8:])
            else:
                continue
                
            files.append({
                'permissions': parts[0],
                'user': parts[2],
                'size': parts[4],
                'date': date,
                'name': name,
            })
        return files

    @action(detail=True, methods=['get'], url_path='file-download')
    def file_download(self, request, pk=None):
        """Download a file from the container."""
        service = self.get_object()
        path = request.query_params.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = _validate_and_sanitize_path(path)
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
            bits, stat = container.get_archive(path)
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
            path = _validate_and_sanitize_path(path)
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
                'k8s_command': ['rm', '-rf', path],
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
            path = _validate_and_sanitize_path(path)
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
                'k8s_command': ['mkdir', '-p', path],
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
            path = _validate_and_sanitize_path(path)
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
                'k8s_command': ['cat', path],
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
        path = request.data.get('path')
        content = request.data.get('content')

        if not path or content is None:
            return Response({'error': 'Path and content parameters are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            path = _validate_and_sanitize_path(path)
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
                'k8s_command': ['sh', '-c', f'cat > {path}'],
            },
            local_action=lambda container, path=None: self._local_file_write(container, path, content),
            path=path,
        )

    def _local_file_write(self, container, path: str, content: str):
        try:
            import tarfile
            import io
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
            path = _validate_and_sanitize_path(path)
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
                'path_suffix': 'file-upload',
                'payload': {'path': path, 'content': base64.b64encode(file_bytes).decode('ascii')},
                'timeout': 60,
                'on_error': lambda resp: Response(
                    {'error': 'Failed to upload file on remote node', 'details': resp.text if resp else 'Timeout'},
                    status=status.HTTP_400_BAD_REQUEST,
                ),
                'k8s_command': ['sh', '-c', f'base64 -d > {path}'],
            },
            local_action=lambda container, path=None: self._local_file_upload(container, path, file_bytes),
            path=path,
        )

    def _local_file_upload(self, container, path: str, file_bytes: bytes):
        try:
            import tarfile
            import io
            import time

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode='w') as tar:
                tarinfo = tarfile.TarInfo(name=os.path.basename(path))
                tarinfo.size = len(file_bytes)
                tarinfo.mtime = int(time.time())
                tar.addfile(tarinfo, io.BytesIO(file_bytes))

            tar_stream.seek(0)
            dir_name = os.path.dirname(path)
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
    throttle_classes = [BurstRateThrottle, DeploymentRateThrottle]

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
        from django.db.models import Q
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
        if self.request.user.is_superuser:
            return base_qs.all()
        
        project_id = self.request.query_params.get('project_id')
        if project_id:
            base_qs = base_qs.filter(service__project_id=project_id)

        return base_qs.filter(
            Q(service__owner=self.request.user) | 
            Q(service__project__team__members__user=self.request.user)
        ).distinct()

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """
        Rollback to this specific deployment.
        Effectively triggers a new deployment using the commit hash/image
        from this one.
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

        # Validate the target deployment
        if not target_deployment.commit_hash:
            return _error_response(
                "ROLLBACK_ARTIFACT_MISSING",
                "Cannot rollback: target deployment has no commit hash.",
                details={"deployment_id": str(target_deployment.id), "service_id": str(service.id)},
                user_action="Choose a deployment that has a valid commit hash/image artifact.",
            )

        if target_deployment.status not in ('ACTIVE', 'SUCCEEDED'):
            return _error_response(
                "ROLLBACK_BLOCKED",
                f"Cannot rollback to a {target_deployment.status} deployment. Only successful deployments can be rolled back to.",
                details={"deployment_id": str(target_deployment.id), "status": target_deployment.status},
                user_action="Pick a previous ACTIVE/SUCCEEDED deployment.",
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

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a deployment that is waiting in REVIEW state."""
        deployment = self.get_object()
        if deployment.status != Deployment.Status.REVIEW:
            return Response({"error": "Deployment is not in REVIEW state"}, status=400)

        serializer = DeploymentApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Apply overrides if any
        service = deployment.service
        overrides = serializer.validated_data
        if 'cpu_cores' in overrides:
            service.cpu_cores = overrides['cpu_cores']
        if 'memory_mb' in overrides:
            service.memory_mb = overrides['memory_mb']
        
        env_overrides = overrides.get('env_overrides', {})
        if env_overrides:
            from .models import EnvironmentVariable
            for k, v in env_overrides.items():
                EnvironmentVariable.objects.update_or_create(
                    service=service, key=k, defaults={'value': v, 'source': 'USER'}
                )
        
        service.save()

        # Resume the deployment
        resume_deploy_task.delay(deployment_id=str(deployment.id))
        
        AuditLog(
            actor=request.user.get_username(),
            action='DEPLOYMENT_APPROVE',
            target=f'Deployment: {deployment.id}',
            metadata={'service_id': str(service.id), 'overrides': list(env_overrides.keys())},
        ).save()

        return Response({"message": "Deployment approved and resumed"})




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
        """
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

            # Prune all unused images (not just dangling) to reclaim disk space
            image_prune_res = client.images.prune(filters={"dangling": ["false"]})
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
                            try:
                                os.remove(f_path)
                            except OSError:
                                pass
                    for d in dirs:
                        d_path = os.path.join(root, d)
                        if os.stat(d_path).st_mtime < now - 3600:
                            try:
                                shutil.rmtree(d_path)
                            except OSError:
                                pass
        except Exception as exc:
            logger.warning("Docker/Temp prune failed during deployment cleanup: %s", exc)

        # ── 3. DB: Delete records ──
        count = base_qs.delete()[0]
        addon_count = addon_qs.delete()[0]

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
        """
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
                # ZH-011 FIX: Verify service ownership before triggering deployment
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

                deployment = Deployment.objects.create(
                    service=service,
                    status=Deployment.Status.QUEUED,
                    commit_hash=serializer.validated_data.get(
                        'commit_hash', 'latest')
                )

                smart_deploy_task.delay(
                    deployment_id=str(deployment.id), 
                    provider_id=str(provider.id),
                    skip_review=skip_review
                )

                return Response({
                    'message': 'Deployment triggered successfully',
                    'deployment_id': deployment.id,
                    'status': deployment.status
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
                    deployment.build_logs += f"\n🧹 Cleaned up container resources."
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
                from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
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
                
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': f"Failed to fetch logs from remote node: HTTP {resp.status_code if resp else 'None'}",
                })
            except Exception as e:
                logger.warning("Failed to proxy runtime logs to remote node: %s", e)
                return Response({
                    'id': str(deployment.id),
                    'runtime_logs': '',
                    'message': f"Remote proxy error: {str(e)}",
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
            return Response({
                'id': str(deployment.id),
                'runtime_logs': '',
                'message': f'Could not fetch runtime logs: {str(e)}',
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
                BrokerOperationalError = tuple()

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

class PlatformResourcesView(GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        import socket
        import shutil
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



class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        """ZH-001 FIX: Filter audit logs to only show entries for the requesting user."""
        if self.request.user.is_superuser:
            qs = AuditLog.objects.all()
        else:
            username = self.request.user.get_username()
            qs = AuditLog.objects.filter(actor=username)

        # Search filter
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(action__icontains=search) |
                Q(actor__icontains=search) |
                Q(target__icontains=search)
            )
        return qs


class SessionTokenView(GenericAPIView):
    """
    Exchange an authenticated Django session for a DRF token.
    Used by the frontend callback page to avoid token-in-URL leakage.
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response({'token': token.key})

class SystemConfigView(GenericAPIView):
    """
    Expose safe server configuration to the frontend.
    GET /api/v1/system/config/
    """
    serializer_class = EmptySerializer
    permission_classes = [permissions.IsAdminUser]

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

        return Response({
            # General
            'VERSION': '3.0.0',
            'DEBUG': settings.DEBUG,
            'DOMAIN': getattr(settings, 'DOMAIN', 'localhost'),
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

            # Safe Update
            'safe_update_available': os.path.exists('/opt/smsly-hosting/scripts/safe-update.sh'),

            # Storage
            **self._get_storage_metrics(),
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

    def post(self, request):
        """Queue a maintenance task via the API."""
        action = str(request.data.get('action') or '').strip().lower()
        action_spec = MAINTENANCE_ACTIONS.get(action)
        if not action_spec:
            return Response(
                {"error": "Invalid maintenance action specified. Use clear, update, or refresh."},
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
            'server_ip': config.server_ip or '',
            'caddy_status': config.caddy_status,
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
        config = PlatformConfig.load()
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
        if 'cloudflare_api_token' in data:
            # Allow explicit clear by sending an empty string.
            config.cloudflare_api_token = str(
                data.get('cloudflare_api_token') or ''
            ).strip()
        clearing_token = 'cloudflare_api_token' in data and not config.cloudflare_api_token
        if 'server_ip' in data:
            config.server_ip = str(data.get('server_ip') or '').strip() or None

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
            from services.caddy_manager import generate_caddyfile, apply_caddyfile
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
            'caddyfile_preview': caddyfile_content,
        })


class RouteRecheckView(GenericAPIView):
    """
    Public route recheck hook for fallback pages.

    Allows a domain-level health recheck without requiring a dashboard login.
    This is intentionally rate-limited and only operates on known service domains.
    """

    serializer_class = EmptySerializer
    permission_classes = [permissions.AllowAny]

    @staticmethod
    def _cors(response: Response):
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    def options(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        return self._cors(Response(status=status.HTTP_204_NO_CONTENT))

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
            return self._cors(
                Response(
                    {"error": f"Invalid domain: {domain_error}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            )

        service = _service_for_domain(domain)
        if not service:
            return self._cors(
                Response(
                    {"error": "Domain is not mapped to a service"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            )

        client_ip = (
            str(request.META.get("HTTP_X_FORWARDED_FOR", "")).split(",")[0].strip()
            or str(request.META.get("REMOTE_ADDR", "unknown")).strip()
            or "unknown"
        )
        throttle_key = f"route-recheck:{service.id}:{client_ip}"
        if cache.get(throttle_key):
            return self._cors(
                Response(
                    {"error": "Recheck already requested. Try again in a few seconds."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            )
        cache.set(throttle_key, True, timeout=20)

        ok, health_status = self._trigger_recheck(service)
        if not ok:
            return self._cors(
                Response(
                    {"error": "Failed to run health recheck"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            )

        return self._cors(
            Response(
                {
                    "message": "Health recheck triggered",
                    "service_id": str(service.id),
                    "health_status": health_status,
                }
            )
        )


class RouteStatusView(GenericAPIView):
    """
    Authenticated DNS/SSL status check for the platform domain.
    """

    authentication_classes = [authentication.SessionAuthentication, authentication.TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):  # pylint: disable=unused-argument
        import socket
        import ssl
        from datetime import datetime

        cfg = PlatformConfig.load()
        domains = [d for d in [cfg.domain] if d]
        entries = []

        def _resolve(host):
            try:
                return socket.gethostbyname(host)
            except Exception:
                return None

        def _cert_expiry(host):
            try:
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(socket.socket(), server_hostname=host) as s:
                    s.settimeout(4.0)
                    s.connect((host, 443))
                    cert = s.getpeercert()
                not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                return not_after.isoformat()
            except Exception:
                return None

        for host in domains:
            resolved = _resolve(host)
            entries.append(
                {
                    "host": host,
                    "resolved_ip": resolved,
                    "matches_server_ip": bool(resolved and cfg.server_ip and resolved == cfg.server_ip),
                    "cert_not_after": _cert_expiry(host) if cfg.use_ssl else None,
                }
            )

        return Response(
            {
                "domain": cfg.domain,
                "use_ssl": cfg.use_ssl,
                "wildcard_subdomains": cfg.wildcard_subdomains,
                "server_ip": cfg.server_ip,
                "entries": entries,
            }
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
        from django.db.models import Q
        qs = self.queryset.filter(
            Q(service__owner=self.request.user) | Q(service__project__team__members__user=self.request.user)
        ).distinct().order_by('-created_at')
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

        # ── Pre-flight safety snapshot check (Fix 4) ─────────────
        # Run a synchronous PRE_TRANSFER snapshot. If it fails, return 422
        # with the snapshot error so the API consumer sees the failure
        # instead of silently losing data on a corrupt restore.
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
                        'Pre-restore safety snapshot failed. Refusing to '
                        'restore to avoid data loss on a corrupt restore.'
                    ),
                    'snapshot_error': str(snap_exc),
                    'backup_id': str(backup.id),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        restore_service_backup_task.delay(
            backup_id=str(backup.id),
            target_service_id=str(target_service_id) if target_service_id else None,
            requesting_user_id=request.user.id,
            raise_on_snapshot_failure=True,
        )
        return Response({'status': 'restore_started'})

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

        backup = self.get_object()
        file_path = backup.file_path

        if not file_path or not os.path.exists(file_path):
            return Response({'error': 'File not found'}, status=status.HTTP_404_NOT_FOUND)

        from .services.backup_service import BackupService
        key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()

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


class ServerBackupViewSet(viewsets.ModelViewSet):
    serializer_class = ServerBackupSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = ServerBackup.objects.all().order_by('-created_at')

    def perform_create(self, serializer):
        backup = serializer.save(status='PENDING')
        create_server_backup_task.delay(backup_id=str(backup.id))

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
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(backup_id=str(backup.id))
        return Response({
            'status': 'restored',
            'warning': (
                'Database dump was not restored. Manual psql restore required. '
                'See docs/DISASTER_RECOVERY.md for the procedure.'
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

        backup = self.get_object()
        file_path = backup.file_path

        if not file_path or not os.path.exists(file_path):
            return Response({'error': 'Backup file not found on disk.'}, status=status.HTTP_404_NOT_FOUND)

        from .services.backup_service import BackupService
        key = os.environ.get("BACKUP_ENCRYPTION_KEY", "").strip()

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

        file_size = os.path.getsize(dest_path)

        # Create a ServerBackup record pointing to the uploaded file
        backup = ServerBackup.objects.create(
            status='COMPLETED',
            file_path=dest_path,
            size_bytes=file_size,
            error_message=f'Uploaded restore from: {uploaded.name}',
        )

        # Trigger async restore
        from apps.deployments.tasks import restore_server_backup_task
        restore_server_backup_task.delay(backup_id=str(backup.id), requesting_user_id=request.user.id)

        return Response({
            'status': 'Restore started from uploaded backup.',
            'backup_id': str(backup.id),
            'file_size': file_size,
        })

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

    def get_queryset(self):
        qs = self.queryset
        if not self.request.user.is_superuser:
            qs = qs.filter(
                Q(service__owner=self.request.user) |
                Q(service__project__team__members__user=self.request.user)
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
from .views_transfer import ServerTransferViewSet


class PlatformResourcesView(GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        import socket
        import shutil
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
