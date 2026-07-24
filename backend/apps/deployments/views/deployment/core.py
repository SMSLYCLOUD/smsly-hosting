"""Deployment viewset - composed from domain-specific mixins."""
import contextlib
import logging
import os

from django.utils import timezone

from rest_framework import parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models import AuditLog, Deployment
from apps.core.rate_limiting import BurstRateThrottle, DeploymentRateThrottle
from ...serializers import (
    DeploymentSerializer, DeploymentTimelineSerializer,
)
from ...services.server_guard import ServerGuard
from ...tasks import (
    smart_deploy_task,
)
from ....teams.permissions import get_team_q_filter
from ....cloud.docker_client import get_docker_client
from .._helpers import (
    _error_response,
    _resolve_provider_for_service,
    is_remote_sync_request,
)
from .actions import LifecycleActionsMixin
from .review import ReviewActionsMixin
from .logs import LogsActionsMixin

logger = logging.getLogger(__name__)


class DeploymentViewSet(LifecycleActionsMixin, ReviewActionsMixin, LogsActionsMixin, viewsets.ModelViewSet):
    """Deployment viewset composed from domain-specific mixins."""
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
        from apps.deployments.models.addons import Addon
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
