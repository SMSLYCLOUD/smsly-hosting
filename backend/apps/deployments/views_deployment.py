import logging
logger = logging.getLogger(__name__)
from .views_service import _resolve_provider_for_service
from .views_service import _error_response
from .views_service import _resolve_provider_for_target
from .views_auth import ZeroTrustHMACAuthentication
from .views_service import _has_active_deployment
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

        SECURITY: the global ``client.images.prune(dangling=False)`` call
        removes every unused image on the host — reaping images other
        tenants' active services depend on. It is restricted to admins.
        Non-admins still get their own failed containers removed and
        dangling-image cleanup.
        """
        from rest_framework.exceptions import PermissionDenied
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
