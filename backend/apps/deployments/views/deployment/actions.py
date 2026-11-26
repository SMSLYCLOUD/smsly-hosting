"""lifecycle mixin."""
import logging
import os
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.teams.permissions import assert_can_write
from ...models import AuditLog, Deployment, Service
from ...serializers import DeploymentSerializer, DeploymentTriggerSerializer
from ...services.server_guard import ServerGuard
from ...tasks import smart_deploy_task
from ....cloud.models import CloudProvider
from .._helpers import (
    _has_active_deployment,
    _resolve_provider_for_service,
)

logger = logging.getLogger(__name__)


def _resolve_deployment_cleanup_server(deployment):
    """Return remote ManagedServer for deployment container cleanup, or None if local."""
    if getattr(deployment, 'target_is_local', False):
        return None
    target_server = getattr(deployment, 'target_server', None)
    if target_server is not None:
        return None if getattr(target_server, 'is_primary', False) else target_server
    svc = getattr(deployment, 'service', None)
    svc_server = getattr(svc, 'server', None) if svc is not None else None
    if svc_server is not None and not getattr(svc_server, 'is_primary', False):
        return svc_server
    return None


def _cleanup_cancelled_containers(deployments) -> list[str]:
    """Remove green/staging containers for cancelled deployments locally or over SSH.

    Never touches any container ID currently referenced by an ACTIVE deployment.
    Returns the list of container IDs removed.
    """
    import shlex

    dep_list = [d for d in deployments if d is not None]
    if not dep_list:
        return []

    service_ids = {d.service_id for d in dep_list if getattr(d, 'service_id', None)}
    live_ids: set[str] = set()
    if service_ids:
        live_ids.update(
            cid for cid in Deployment.objects.filter(
                service_id__in=service_ids,
                status=Deployment.Status.ACTIVE,
            ).values_list('container_id', flat=True)
            if cid
        )
        live_ids.update(
            cid for cid in Deployment.objects.filter(
                service_id__in=service_ids,
                status=Deployment.Status.ACTIVE,
            ).values_list('green_container_id', flat=True)
            if cid
        )

    local_cids: list[str] = []
    remote_by_server: dict[str, tuple[object, list[str]]] = {}
    for dep in dep_list:
        candidates = [
            cid for cid in (
                getattr(dep, 'green_container_id', None),
                getattr(dep, 'container_id', None),
            )
            if cid and cid not in live_ids
        ]
        if not candidates:
            continue
        server = _resolve_deployment_cleanup_server(dep)
        if server is None:
            for cid in candidates:
                if cid not in local_cids:
                    local_cids.append(cid)
        else:
            key = str(server.id)
            if key not in remote_by_server:
                remote_by_server[key] = (server, [])
            for cid in candidates:
                if cid not in remote_by_server[key][1]:
                    remote_by_server[key][1].append(cid)

    cleaned: list[str] = []
    if local_cids:
        try:
            import docker
            client = docker.from_env()
            for c_id in local_cids:
                try:
                    container = client.containers.get(c_id)
                    container.remove(force=True)
                    cleaned.append(c_id)
                    logger.info("Cleaned up cancelled local container %s", c_id)
                except docker.errors.NotFound:
                    pass
                except Exception as exc:
                    logger.error("Failed to cleanup local container %s: %s", c_id, exc)
        except Exception as exc:
            logger.error("Docker client error during cancel cleanup: %s", exc)

    if remote_by_server:
        from ...services.ssh_client import SSHClient
        for server, cids in remote_by_server.values():
            if not (getattr(server, 'ssh_key', None) or getattr(server, 'ssh_password', None)):
                logger.warning(
                    "Skipping remote container cleanup on %s: no SSH credentials",
                    getattr(server, 'name', '?'),
                )
                continue
            ssh = SSHClient(
                ip=server.host,
                key_content=server.ssh_key,
                password=server.ssh_password,
                user=getattr(server, 'ssh_user', 'root') or 'root',
                port=getattr(server, 'ssh_port', 22) or 22,
                key_passphrase=getattr(server, 'ssh_key_passphrase', '') or '',
                wg_address=getattr(server, 'wg_address', '') or '',
            )
            try:
                quoted = " ".join(shlex.quote(c) for c in cids)
                _, err, code = ssh.exec_command(
                    f"docker rm -f {quoted} 2>/dev/null || true",
                    raise_on_error=False,
                    timeout=30,
                )
                if code == 0:
                    cleaned.extend(cids)
                    logger.info("Cleaned up %d cancelled remote container(s) on %s", len(cids), server.name)
                else:
                    logger.warning("Remote container cleanup warning on %s: %s", server.name, err)
            except Exception as exc:
                logger.warning("Remote container cleanup failed on %s: %s", getattr(server, 'name', '?'), exc)
            finally:
                ssh.close()

    return cleaned


class LifecycleActionsMixin:
    """LifecycleActions actions for the viewset."""


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
        from ...models.core import Project
        from ...models.registry_scope import ScopedRegistry

        serializer = DeploymentTriggerSerializer(data=request.data)
        if serializer.is_valid():
            service_id = serializer.validated_data['service_id']
            provider_id = serializer.validated_data['provider_id']
            if serializer.validated_data.get('skip_review', False) or serializer.validated_data.get('fast_deploy', False):
                return Response(
                    {'error': 'skip_review and fast_deploy are reserved for trusted internal deployment paths.'},
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
    def retry(self, request, pk=None):
        """
        Re-queue a failed deployment.
        POST /api/v1/deployments/{id}/retry/
        POST /api/v1/cloud/deployments/{id}/retry/  (alias)

        Body (optional): { "docker_recovery": true } — run a fresh
        Docker/containerd recovery (builder prune, ingest + tmpmounts
        clear, daemon restart) synchronously BEFORE re-queueing, so the
        retry does not hit the same corrupted layer state. Approved for
        BUILD_FAILED rows showing containerd corruption; the restart
        kills running containers (compose restarts them).
        """
        from django.core.cache import cache

        deployment = self.get_object()
        assert_can_write(request.user, deployment.service, action='retry deployment')
        if deployment.status not in (
            Deployment.Status.FAILED,
            Deployment.Status.CANCELLED,
            Deployment.Status.BUILD_FAILED,
        ):
            return Response(
                {'error': f'Cannot retry deployment in {deployment.status} status.'},
                status=status.HTTP_409_CONFLICT,
            )
        recovery_result = None
        if bool(request.data.get('docker_recovery', False)):
            if not (getattr(request.user, 'is_staff', False) or getattr(request.user, 'is_superuser', False)):
                return Response(
                    {'error': 'docker_recovery requires staff/superuser privileges.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            from ...tasks.build_recovery import (
                _PRUNE_CACHE_KEY,
                is_build_corruption_error,
                perform_docker_recovery,
            )

            logs_text = str(deployment.build_logs or "")
            if not is_build_corruption_error(logs_text):
                return Response(
                    {'error': 'No containerd corruption signature in build logs — plain retry instead.'},
                    status=status.HTTP_409_CONFLICT,
                )
            # Explicit operator request bypasses the 5-minute auto-recovery
            # cooldown so a known-corrupt daemon heals immediately.
            cache.delete(_PRUNE_CACHE_KEY)
            try:
                recovery_result = perform_docker_recovery(
                    deployment_id=str(deployment.id)
                )
            except Exception as exc:
                logger.warning("Retry-time docker recovery failed: %s", exc)
                recovery_result = {"status": "error", "reason": str(exc)[:300]}
            try:
                from django.utils import timezone as _tz

                cache.set(
                    _PRUNE_CACHE_KEY,
                    str(_tz.now().timestamp()),
                    300,
                )
            except Exception:
                pass
            steps = (recovery_result or {}).get("steps", {})
            deployment.build_logs = (
                f"{deployment.build_logs or ''}"
                f"\n[Recovery] Operator-triggered docker recovery before retry: "
                f"{steps}.\n"
            )
        deployment.status = Deployment.Status.QUEUED
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Re-queued by user retry at {timezone.now().isoformat()}.\n"
        )
        deployment.save(update_fields=['status', 'build_logs', 'updated_at'])
        provider = _resolve_provider_for_service(deployment.service)
        if provider:
            smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=str(provider.id))
        payload = DeploymentSerializer(deployment).data
        if recovery_result is not None:
            payload['docker_recovery'] = recovery_result
        return Response(payload)


    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancel a queued or building deployment.
        POST /api/v1/deployments/{id}/cancel/
        """
        deployment = self.get_object()
        assert_can_write(request.user, deployment.service, action='cancel deployment')

        if deployment.status not in (
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.AWAITING_APPROVAL,
            Deployment.Status.STAGED,
        ):
            return Response(
                {'error': f'Cannot cancel deployment in {deployment.status} '
                          f'status. Only QUEUED, REVIEW, BUILDING, AWAITING_APPROVAL, '
                          f'or STAGED deployments can be cancelled.'},
                status=status.HTTP_409_CONFLICT)

        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs += "\n\n[Cancelled] Deployment cancelled by user."

        # Revoke the worker task: a DB flag alone leaves a delivered task
        # running (2026-09-26: cancelled duplicates collided on promote).
        # terminate=True stops a running build; best-effort, never fatal.
        try:
            task_id = getattr(deployment, 'celery_task_id', '') or ''
            if task_id:
                from celery import current_app as _celery_app
                _celery_app.control.revoke(task_id, terminate=True, signal='SIGTERM')
                deployment.build_logs += "\n[Cancelled] Worker task revoked."
                logger.info("Revoked celery task %s for cancelled deployment %s", task_id, deployment.id)
        except Exception as exc:
            logger.warning("Celery revoke failed for deployment %s: %s", deployment.id, exc)

        # Clean up any running containers associated with this deployment (local or remote)
        cleaned = _cleanup_cancelled_containers([deployment])
        if cleaned:
            deployment.build_logs += "\n🧹 Cleaned up container resources."

        # A cancelled green must never keep receiving canary traffic: drop
        # the Traefik split file (no-op when no split is active).
        try:
            from ...services.traefik_manager.canary_file import remove_canary_file
            remove_canary_file(deployment.service)
        except Exception as exc:
            logger.debug("Canary file cleanup on cancel failed: %s", exc)

        deployment.save()

        # Regenerate Caddyfile if this was a STAGED deployment (remove
        # the staging domain from the @known_hosts wildcard matcher).
        if deployment.status == Deployment.Status.CANCELLED:
            try:
                from ...tasks.deploy.helpers import _regenerate_caddyfile
                _regenerate_caddyfile()
            except Exception as exc:
                logger.warning("Failed to regenerate Caddyfile after cancel: %s", exc)

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

        with transaction.atomic():
            # Only allow cancelling deployments the user owns; lock matching rows
            # so concurrent status transitions and audit writes commit atomically.
            locked_qs = self.get_queryset().select_for_update().filter(
                id__in=deployment_ids,
                status__in=[
                    Deployment.Status.QUEUED,
                    Deployment.Status.REVIEW,
                    Deployment.Status.BUILDING,
                    Deployment.Status.AWAITING_APPROVAL,
                    Deployment.Status.STAGED,
                ],
            )
            cancelled_deps = list(
                locked_qs.select_related('service', 'service__server', 'target_server')
            )
            checked_service_ids = set()
            for dep in cancelled_deps:
                if dep.service_id and dep.service_id not in checked_service_ids:
                    checked_service_ids.add(dep.service_id)
                    assert_can_write(request.user, dep.service, action='cancel deployment')
            locked_ids = [d.id for d in cancelled_deps]
            affected_service_ids = list({d.service_id for d in cancelled_deps})
            task_ids = [d.celery_task_id for d in cancelled_deps if d.celery_task_id]
            if locked_ids:
                count = Deployment.objects.filter(id__in=locked_ids).update(
                    status=Deployment.Status.CANCELLED,
                    finished_at=timezone.now(),
                )
                if count:
                    AuditLog(
                        actor=request.user.get_username(),
                        action="DEPLOYMENT_BULK_CANCEL",
                        target="Deployment: multiple",
                        metadata={
                            "count": count,
                            "deployment_ids": [str(d) for d in deployment_ids],
                        },
                    ).save()
            else:
                count = 0

        try:
            if task_ids:
                from celery import current_app as _celery_app

                for tid in set(task_ids):
                    try:
                        _celery_app.control.revoke(tid, terminate=True, signal="SIGTERM")
                    except Exception:
                        pass
                logger.info("Revoked %d worker task(s) on bulk-cancel", len(set(task_ids)))
        except Exception as exc:
            logger.warning("Celery bulk-revoke failed: %s", exc)

        # Clean up green/staging containers (local and remote) and stop canary traffic.
        _cleanup_cancelled_containers(cancelled_deps)
        try:
            from ...services.traefik_manager.canary_file import remove_canary_file

            for service_id in affected_service_ids:
                remove_canary_file(str(service_id))
        except Exception as exc:
            logger.debug("Canary file cleanup on bulk-cancel failed: %s", exc)

        return Response({
            'cancelled': count,
            'message': f'{count} deployment(s) cancelled.',
        })


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

        # Security: Validate zip magic bytes
        from ..upload_security import validate_zip_magic, validate_zip_entries, validate_zip_no_bomb
        magic_err = validate_zip_magic(uploaded_file)
        if magic_err:
            return magic_err

        # Security: Check for zip-slip (path traversal)
        is_safe, err_msg = validate_zip_entries(uploaded_file)
        if not is_safe:
            return Response(
                {'error': f'Unsafe archive (unsafe entries): {err_msg}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Security: Protect against zip bombs
        bomb_err = validate_zip_no_bomb(uploaded_file)
        if bomb_err:
            return bomb_err

        try:
            from apps.teams.permissions import assert_can_write, get_team_q_filter
            service = Service.objects.filter(
                get_team_q_filter(request.user)
            ).distinct().get(id=service_id)
            assert_can_write(request.user, service, action='upload deployment source')

            # Security: Use per-tenant isolated upload directory
            import secrets
            upload_dir = os.path.join('/app/var/uploads', str(request.user.id))
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

    @action(detail=True, methods=['post'])
    def promote(self, request, pk=None):
        """
        Promote a STAGED deployment to ACTIVE (swap green container to live).
        POST /api/v1/deployments/{id}/promote/
        """
        deployment = self.get_object()
        assert_can_write(request.user, deployment.service, action='promote deployment')

        if deployment.status != Deployment.Status.STAGED:
            return Response(
                {'error': f'Cannot promote deployment in {deployment.status} status. '
                          'Only STAGED deployments can be promoted.'},
                status=status.HTTP_409_CONFLICT)

        if deployment.service.deploy_mode == 'COMPOSE':
            return Response(
                {
                    'error': 'Compose staging is isolated, but project-level promotion '
                             'is not yet supported. Cancel this staged deployment or '
                             'deploy it through the normal live flow.'
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not deployment.green_container_id:
            return Response(
                {'error': 'No green container found on this deployment.'},
                status=status.HTTP_400_BAD_REQUEST)

        from ...tasks.deploy.helpers import _do_promote
        from ...tasks.deploy.provider import _resolve_provider_for_service

        provider = _resolve_provider_for_service(deployment.service, prefer_local=True)
        if not provider:
            return Response(
                {'error': 'No active cloud provider configured'},
                status=status.HTTP_400_BAD_REQUEST)

        from ...services.safedeploy.promotion_guard import check_promotion_readiness
        readiness = check_promotion_readiness(deployment, provider=provider)
        if not readiness['ready']:
            return Response(
                {
                    'error': 'Promotion blocked by readiness policy.',
                    'blockers': readiness['blockers'],
                    'warnings': readiness['warnings'],
                },
                status=status.HTTP_409_CONFLICT)

        try:
            _do_promote(deployment, provider)
        except Exception as exc:
            logger.exception("Promote failed for deployment %s: %s", deployment.id, exc)
            try:
                from apps.deployments.utils import append_log as _append_log
                _append_log(deployment, f"\n[PROMOTE-FAILED] Manual promote failed: {exc}\n")
            except Exception:
                pass
            return Response(
                {'error': f'Promote failed: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'message': 'Deployment promoted to ACTIVE',
            'warnings': readiness['warnings'],
            'deployment': DeploymentSerializer(deployment).data,
        })
