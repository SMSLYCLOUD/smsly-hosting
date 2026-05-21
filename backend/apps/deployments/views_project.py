"""
Views for Project CRUD and nested service management.
"""

import logging

from rest_framework import viewsets, permissions, status, serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q

from .models_project import Project
from .models import Service
from .serializers import ServiceSerializer

logger = logging.getLogger(__name__)


class ProjectViewSet(viewsets.ModelViewSet):
    """
    CRUD for Projects.  Owner-scoped — users can only see/manage their own.

    Endpoints:
        GET    /api/v1/projects/                    — list
        POST   /api/v1/projects/                    — create
        GET    /api/v1/projects/{id}/                — detail
        PATCH  /api/v1/projects/{id}/                — update
        DELETE /api/v1/projects/{id}/                — delete
        GET    /api/v1/projects/{id}/services/       — services in project
        POST   /api/v1/projects/{id}/move-service/   — move service into project
        POST   /api/v1/projects/{id}/remove-service/ — remove service from project
    """
    permission_classes = [permissions.IsAuthenticated]

    # Serializer defined inline to keep it co-located with the viewset

    class ProjectSerializer(drf_serializers.ModelSerializer):
        services_count = drf_serializers.SerializerMethodField()
        latest_deploy_status = drf_serializers.SerializerMethodField()
        latest_deploy_at = drf_serializers.SerializerMethodField()

        class Meta:
            model = Project
            fields = [
                'id', 'name', 'slug', 'description',
                'icon_emoji', 'color', 'is_default',
                'services_count', 'latest_deploy_status', 'latest_deploy_at',
                'created_at', 'updated_at',
            ]
            read_only_fields = ['id', 'slug', 'created_at', 'updated_at']
            # Disable DRF's auto UniqueTogetherValidator for (owner, slug)
            # because slug is auto-generated + deduplicated in Project.save()
            validators = []

        def get_services_count(self, obj):
            return obj.services.count()

        def get_latest_deploy_status(self, obj):
            """Aggregate: status of the most recent deploy across all project services."""
            from .models import Deployment
            dep = (
                Deployment.objects
                .filter(service__project=obj)
                .order_by('-created_at')
                .values('status')
                .first()
            )
            return dep['status'] if dep else None

        def get_latest_deploy_at(self, obj):
            from .models import Deployment
            dep = (
                Deployment.objects
                .filter(service__project=obj)
                .order_by('-created_at')
                .values('created_at')
                .first()
            )
            return dep['created_at'].isoformat() if dep else None

    serializer_class = ProjectSerializer

    def get_queryset(self):
        """Owner and Team scoped: users see their own projects and team projects."""
        qs = Project.objects.all().order_by('id')
        if self.request.user.is_superuser:
            return qs
        return qs.filter(
            Q(owner=self.request.user) |
            Q(team__members__user=self.request.user)
        ).distinct().order_by('id')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    # ── Nested: services in project ──────────────────────────────

    @action(detail=True, methods=['get'], url_path='services')
    def project_services(self, request, pk=None):
        """GET /api/v1/projects/{id}/services/ — list services in this project."""
        project = self.get_object()
        services = Service.objects.filter(
            project=project,
        ).order_by('-updated_at')
        serializer = ServiceSerializer(services, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='move-service')
    def move_service(self, request, pk=None):
        """
        POST /api/v1/projects/{id}/move-service/
        Body: { "service_id": "uuid" }

        Moves a service into this project.  The service must belong to the
        requesting user.
        """
        project = self.get_object()
        service_id = request.data.get('service_id')
        if not service_id:
            return Response(
                {'error': 'service_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service = Service.objects.get(id=service_id, owner=request.user)
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found or not owned by you'},
                status=status.HTTP_404_NOT_FOUND,
            )

        service.project = project
        service.save(update_fields=['project', 'updated_at'])

        logger.info(
            'Moved service %s (%s) into project %s (%s)',
            service.name, service.id, project.name, project.id,
        )
        return Response({
            'status': 'ok',
            'service_id': str(service.id),
            'project_id': str(project.id),
        })

    @action(detail=True, methods=['post'], url_path='remove-service')
    def remove_service(self, request, pk=None):
        """
        POST /api/v1/projects/{id}/remove-service/
        Body: { "service_id": "uuid" }

        Removes a service from this project (sets project=null).
        """
        project = self.get_object()
        service_id = request.data.get('service_id')
        if not service_id:
            return Response(
                {'error': 'service_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service = Service.objects.get(
                id=service_id, owner=request.user, project=project)
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found in this project'},
                status=status.HTTP_404_NOT_FOUND,
            )

        service.project = None
        service.save(update_fields=['project', 'updated_at'])

        logger.info(
            'Removed service %s (%s) from project %s (%s)',
            service.name, service.id, project.name, project.id,
        )
        return Response({
            'status': 'ok',
            'service_id': str(service.id),
        })

    @action(detail=True, methods=['post'], url_path='sync-envs')
    def sync_envs(self, request, pk=None):
        """
        POST /api/v1/projects/{id}/sync-envs/
        
        Hardens and synchronizes environment variables across all services in the ecosystem.
        Deterministic linking of Intelligence, Security, and Core services.
        """
        project = self.get_object()
        from services.ecosystem import sync_ecosystem_envs
        
        try:
            logger.info("Triggering instant ecosystem sync for project %s (%s)", project.name, project.id)
            result = sync_ecosystem_envs(str(project.id))
            return Response(result)
        except Exception as e:
            logger.exception("Ecosystem sync failed for project %s", project.id)
            return Response(
                {"error": f"Sync failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
