import logging
logger = logging.getLogger(__name__)
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

from .tasks import _IN_PROGRESS_DEPLOYMENT_STATUSES

from .tasks import _IN_PROGRESS_DEPLOYMENT_STATUSES
from .views_auth import CaddySecretOrAdminPermission
from .views_domains import _parse_bool
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
from .views_files import ServiceFileActionsMixin
from .views_envvars import ServiceEnvVarActionsMixin
from .views_domains import ServiceDomainActionsMixin
from .views_ai_router import ServiceAIRouterActionsMixin


def _check_tier_gates_disabled() -> bool:
    """Return True if the SMSLY_DISABLE_TIER_GATES bypass is active.

    On the first consult in a given process where the flag is on,
    record an immutable AuditLog entry so the bypass is never silent.
    """
    global _TIER_GATES_LOGGED
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


from .tasks import _IN_PROGRESS_DEPLOYMENT_STATUSES

class ServiceViewSet(ServiceFileActionsMixin, ServiceEnvVarActionsMixin, ServiceDomainActionsMixin, ServiceAIRouterActionsMixin, viewsets.ModelViewSet):
    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return self.queryset.none()
        return self.queryset.filter(
            get_team_q_filter(user)
        ).select_related('project').prefetch_related('deployments')


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
        assert_can_write(self.request.user)
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
        assert_can_write(self.request.user, service)
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
        ).order_by('-created_at').prefetch_related(
            models.Prefetch('deployments', queryset=Deployment.objects.order_by('-created_at'))
        )

        data = []
        for preview in previews:
            deploys = list(preview.deployments.all()[:1])
            latest_deploy = deploys[0] if deploys else None
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


    def get_permissions(self):
        """Hardened auth for the Caddy ask endpoint: shared secret OR admin user."""
        if self.action == 'check_domain':
            return [CaddySecretOrAdminPermission()]
        return super().get_permissions()


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
