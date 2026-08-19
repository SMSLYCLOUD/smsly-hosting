"""Service viewset - composed from domain-specific mixins."""
import logging
import threading

from django.utils import timezone

from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from django.db.models import Prefetch

from ...models import Deployment, Service
from ...models.audit import AuditLog
from apps.core.rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from ...serializers import DeploymentSerializer, ServiceSerializer, ServiceListSerializer
from ...services.server_guard import ServerGuard
from ...tasks import smart_deploy_task
from .._helpers import (
    CaddySecretOrAdminPermission, _ensure_local_server_record, _parse_bool,
    _resolve_provider_for_service, _setup_provider_webhook, is_remote_sync_request,
)
from apps.teams.permissions import assert_can_delete, assert_can_write, get_team_q_filter

logger = logging.getLogger(__name__)
from .deploy import DeployActionsMixin
from .domains import DomainActionsMixin
from .envvars import EnvVarActionsMixin
from .files import FileBrowserActionsMixin
from .ai_router import AIRouterActionsMixin
from .previews import PreviewActionsMixin
from .incident import IncidentMixin
from .meta import MetaActionsMixin


class ServiceViewSet(DeployActionsMixin, DomainActionsMixin, EnvVarActionsMixin, FileBrowserActionsMixin, AIRouterActionsMixin, PreviewActionsMixin, IncidentMixin, MetaActionsMixin, viewsets.ModelViewSet):
    """Service viewset composed from domain-specific mixins."""
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


    def _optimize_queryset(self, qs):
        from django.db.models import Count, Q
        from apps.autoscaler.models.replica import ServiceReplica
        return qs.select_related('project', 'owner', 'server').prefetch_related(
            Prefetch(
                'deployments',
                queryset=Deployment.objects.filter(
                    status=Deployment.Status.ACTIVE
                ).order_by('-created_at')[:1],
                to_attr='_active_deployments',
            ),
            Prefetch(
                'deployments',
                queryset=Deployment.objects.order_by('-created_at')[:1],
                to_attr='_prefetched_deployments',
            ),
            'domain_instances',
        ).annotate(
            running_replicas_count=Count(
                'servicereplica',
                filter=Q(servicereplica__status='RUNNING'),
            )
        )

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return self.queryset.none()
        if user.is_superuser or is_remote_sync_request(self.request):
            return self._optimize_queryset(self.queryset.all())
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
        return self._optimize_queryset(qs)

    def get_serializer_class(self):
        if self.action == 'list':
            return ServiceListSerializer
        return ServiceSerializer

    def perform_create(self, serializer):
        project = serializer.validated_data.get('project')
        if project:
            assert_can_write(self.request.user, project, action='create service in')
        from ...models.core import ManagedServer
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
        from ...models.core import ManagedServer

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
        from ...models.core import Service
        from ...services.deletion_orchestrator import DeletionOrchestrator

        orchestrator = DeletionOrchestrator()
        success = orchestrator.delete_service_resources(instance, force=force)
        if success:
            service_id = str(instance.id)
            service_name = instance.name
            instance.delete()
            self._sync_caddy()
            return Response(
                {
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
                "status": "deletion_failed",
                "error": instance.deletion_error,
                "resource_id": str(instance.id),
                "force": force,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


    def perform_destroy(self, instance, force=False):
        """Set status to pending and queue async deletion."""
        from ...models.core import Service
        from ...tasks import delete_service_task

        instance.status = Service.Status.DELETION_PENDING
        instance.save(update_fields=['status'])

        from ...utils import log_event
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
                logger.error("Resource cleanup failed during force-purge of %s: %s", instance.id, exc)
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
        from ...models.core import Service
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
                logger.error("Resource cleanup failed during retry force-purge of %s: %s", instance.id, exc)
            try:
                instance.delete()
                logger.info("Force-purge complete for service %s via retry-delete.", instance.id)
            except Exception as exc:
                logger.error("Force-purge DB deletion failed for %s: %s", instance.id, exc)
            self._sync_caddy()
            return Response({"message": "Force-purge complete.", "force": force}, status=status.HTTP_200_OK)

        from ...tasks import delete_service_task
        delete_service_task.delay(str(instance.id), force=force)

        return Response({"message": "Retry cleanup initiated.", "force": force}, status=status.HTTP_202_ACCEPTED)


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
            from apps.deployments.utils.target import resolve_active_execution_target
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
            logger.error("Stop resolution/remote call failed for service %s: %s", service.id, e)

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
        from apps.core.services.health_monitor import reset_restart_state
        reset_restart_state(str(service.id))

        # ── Fast restart path: just docker restart the container ──
        if not force_rebuild:
            try:
                from apps.deployments.utils.target import (
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
                        logger.error("Fast remote restart failed for %s. Falling back to full rebuild.", service.name)
                except Exception as exc:
                    logger.error("Fast remote restart request failed: %s", exc)

            elif container_id:
                try:
                    from apps.deployments.services.container_runtime import ContainerRuntime
                    runtime = ContainerRuntime()
                    runtime.restart_container(container_id)

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
            from apps.core.services.health_monitor import (
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

            exit_code = state.get('ExitCode')
            restart_count = int(state.get('RestartCount') or 0)

            # Also fetch saved crash logs from latest deployment
            from apps.deployments.models import Deployment as DepModel
            latest_deploy = DepModel.objects.filter(service=service).order_by("-created_at").first()
            saved_logs = latest_deploy.build_logs[-2000:] if latest_deploy and latest_deploy.build_logs else ""

            return Response({
                'service_id': str(service.id),
                'service_name': service.name,
                'status': effective_status,
                'running': runtime_status == 'running',
                'health': health_status or None,
                'container_id': container.id,
                'container_name': container.name,
                'image': ','.join(getattr(container.image, 'tags', []) or []),
                'exit_code': exit_code,
                'restart_count': restart_count,
                'saved_logs': saved_logs,
                'saved_logs_source': 'build_logs' if saved_logs else None,
            })
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Service runtime status failed for %s: %s", service.id, exc)
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

    def _is_remote_sync_request(self):
        return is_remote_sync_request(self.request)
