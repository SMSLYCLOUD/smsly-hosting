import logging
logger = logging.getLogger(__name__)
from .tasks import MAINTENANCE_ACTIONS
from .views_domains import _service_for_domain
from .views_domains import _normalize_request_domain
from .views_auth import EmptySerializer
import os
import posixpath
import hmac
import re
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
from django.db import DataError, IntegrityError, transaction, models
from django.db.models import Q, Count, Avg, F, ExpressionWrapper, DurationField
from django.utils.http import content_disposition_header
from django.core import signing
from apps.deployments.services.github_webhooks import setup_github_webhook
from apps.deployments.services.gitlab_webhooks import setup_gitlab_webhook
from apps.deployments.services.bitbucket_webhooks import setup_bitbucket_webhook
import threading
from .ai_router import DEFAULT_AI_ROUTER_API_BASE, DEFAULT_AI_ROUTER_UI_BASE, DEFAULT_BRAID_ALIAS, is_ai_router_service, persist_ai_router_config, serialize_ai_router_config
from .models import Service, Deployment, EnvironmentVariable, PlatformConfig
from .serializers import ServiceSerializer, DeploymentSerializer, DeploymentTriggerSerializer, EnvVarSerializer, DeploymentTimelineSerializer, InstantRollbackSerializer, AuditLogSerializer, DeploymentApproveSerializer, ServiceBackupSerializer, ServerBackupSerializer, BackupScheduleSerializer
from .models_audit import AuditLog
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .tasks import smart_deploy_task, resume_deploy_task, create_service_backup_task, create_server_backup_task, restore_service_backup_task, enqueue_smart_deploy_task
from .rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from .domain_utils import normalize_domain
from .services.server_guard import ServerGuard
from apps.cloud.models import CloudProvider
import uuid
import logging
import re
from celery.result import AsyncResult
from apps.cloud.docker_client import get_docker_client
from .utils import validate_and_sanitize_path
from apps.deployments.utils import resolve_running_container
from apps.teams.permissions import get_team_q_filter, assert_can_write, assert_can_delete, user_can_read
from .views_audit import AuditLogViewSet
from .views_auth import SessionTokenView
from .views_route_status import RouteStatusView
from .views_transfer import ServerTransferViewSet


_CADDYFILE_REDACT_KEYWORDS = (
    'Strict-Transport-Security',
    'tls',
    'internal',
    'basicauth',
    'header Strict-Transport-Security',
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

        safe_data = {
            'VERSION': '3.0.0',
            'DOMAIN': getattr(settings, 'DOMAIN', 'localhost'),
            'safe_update_available': os.path.exists('/opt/smsly-hosting/scripts/safe-update.sh'),
            **self._get_storage_metrics(),
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
            if in_secret_block < 0:
                in_secret_block = 0
            continue
        if env_var_re.search(stripped):
            redacted_lines.append('***REDACTED***')
            continue
        redacted_lines.append(line)
    return "\n".join(redacted_lines)
