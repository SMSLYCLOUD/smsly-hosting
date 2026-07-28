"""preview mixin."""
import logging
import re

from django.db import models, transaction
from django.db.utils import IntegrityError

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from ...models import Deployment, Service
from ...serializers import DeploymentSerializer, ServiceSerializer
from ...tasks import smart_deploy_task
from .._helpers import _resolve_provider_for_service
from apps.teams.permissions import assert_can_write

logger = logging.getLogger(__name__)


class PreviewActionsMixin:
    """PreviewActions actions for the viewset."""

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
        assert_can_write(request.user, parent, action='create preview for')
        branch = request.data.get('branch') or request.data.get('branch_name')
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

                # Option A enterprise preview: clean start, do not copy parent env vars to prevent leaks and blast radius

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
                {'error': f'Failed to create preview: {exc!s}'},
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
                'service': str(parent.id),
                'name': preview.name,
                'branch': preview.branch,
                'branch_name': preview.branch,
                'commit_sha': latest_deploy.commit_hash if latest_deploy else '',
                'pr_number': preview.pr_number,
                'preview_url': preview.service_url,
                'health_status': preview.health_status,
                'status': latest_deploy.status if latest_deploy else (preview.health_status or 'UNKNOWN'),
                'created_at': preview.created_at.isoformat(),
                'updated_at': preview.updated_at.isoformat() if hasattr(preview, 'updated_at') and preview.updated_at else preview.created_at.isoformat(),
                'latest_deployment': {
                    'id': str(latest_deploy.id),
                    'status': latest_deploy.status,
                    'created_at': latest_deploy.created_at.isoformat(),
                } if latest_deploy else None,
            })

        return Response({'count': len(data), 'results': data})


    @action(detail=True, methods=['delete', 'post'], url_path='destroy-preview')
    def destroy_preview(self, request, pk=None):
        """
        Destroy a preview environment.
        DELETE/POST /api/v1/services/{id}/destroy-preview/
        Body: { "preview_id": "uuid" }
        """
        parent = self.get_object()
        assert_can_write(request.user, parent, action='destroy preview for')
        preview_id = request.data.get('preview_id') or request.query_params.get('preview_id')

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
