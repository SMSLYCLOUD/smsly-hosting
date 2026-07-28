"""lifecycle mixin."""
import logging
import os
import uuid

from django.conf import settings
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

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
        from ...models.project import Project
        from ...models.registry_scope import ScopedRegistry

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
    def retry(self, request, pk=None):
        """
        Re-queue a failed deployment.
        POST /api/v1/deployments/{id}/retry/
        POST /api/v1/cloud/deployments/{id}/retry/  (alias)
        """
        deployment = self.get_object()
        if deployment.status not in (Deployment.Status.FAILED, Deployment.Status.CANCELLED):
            return Response(
                {'error': f'Cannot retry deployment in {deployment.status} status.'},
                status=status.HTTP_409_CONFLICT,
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
        return Response(DeploymentSerializer(deployment).data)


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
                        logger.error(f"Failed to cleanup container {c_id}: {e}")
                if cleaned_any:
                    deployment.build_logs += "\n🧹 Cleaned up container resources."
        except Exception as e:
            logger.error(f"Docker client error during cancel cleanup: {e}")

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
                {'error': f'Unsafe archive: {err_msg}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Security: Protect against zip bombs
        bomb_err = validate_zip_no_bomb(uploaded_file)
        if bomb_err:
            return bomb_err

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
