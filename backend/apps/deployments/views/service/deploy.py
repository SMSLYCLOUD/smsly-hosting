"""deploy mixin."""
import logging

from django.db import transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from ...models import Deployment
from ...models.audit import AuditLog
from ...serializers import DeploymentSerializer
from ...services.server_guard import ServerGuard
from ...tasks import enqueue_smart_deploy_task, smart_deploy_task
from .._helpers import (
    _error_response, _has_active_deployment, _parse_bool,
    _resolve_provider_for_service, _resolve_provider_for_target,
    _resolve_requested_deploy_target,
)
from apps.teams.permissions import assert_can_write

logger = logging.getLogger(__name__)


from rest_framework import permissions
from ...serializers import InstantRollbackSerializer


class DeployActionsMixin:
    """DeployActions actions for the viewset."""


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
        from ...models.servers import ManagedServer

        service = self.get_object()
        assert_can_write(request.user, service, action='multi-deploy')
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
            from ...models.project import Project
            from ...models.registry_scope import ScopedRegistry

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
        assert_can_write(request.user, service, action='instant rollback')
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
                        from apps.deployments.tasks.deployment.tasks_deploy import enqueue_smart_deploy_task
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
