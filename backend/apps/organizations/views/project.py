"""
Views for Project CRUD and nested service management.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from rest_framework import permissions, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.permissions.codes import MEMBER_INVITE, MEMBER_REMOVE, MEMBER_ROLE_CHANGE
from apps.permissions.utils import has_permission

from apps.deployments.models import Service  # type: ignore[attr-defined]
from ..models.project import Project, ProjectMember  # type: ignore[attr-defined]
from apps.deployments.serializers import ServiceSerializer

logger = logging.getLogger(__name__)


class RemoveServiceSerializer(drf_serializers.Serializer):
    service_id = drf_serializers.UUIDField()
    replacement_project_id = drf_serializers.UUIDField()


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
                'icon_emoji', 'color', 'is_default', 'is_ephemeral',
                'services_count', 'latest_deploy_status', 'latest_deploy_at',
                'created_at', 'updated_at',
            ]
            read_only_fields = ['id', 'slug', 'created_at', 'updated_at']
            # Disable DRF's auto UniqueTogetherValidator for (owner, slug)
            # because slug is auto-generated + deduplicated in Project.save()
            validators: list = []

        def get_services_count(self, obj):
            counts = getattr(self, '_service_counts', None)
            if counts is not None:
                return counts.get(obj.id, 0)
            return obj.services.count()

        def get_latest_deploy_status(self, obj):
            statuses = getattr(self, '_latest_statuses', None)
            if statuses is not None:
                return statuses.get(obj.id)
            from apps.deployments.models import Deployment
            dep = (
                Deployment.objects
                .filter(service__project=obj)
                .order_by('-created_at')
                .values('status')
                .first()
            )
            return dep['status'] if dep else None

        def get_latest_deploy_at(self, obj):
            times = getattr(self, '_latest_times', None)
            if times is not None:
                return times.get(obj.id)
            from apps.deployments.models import Deployment
            dep = (
                Deployment.objects
                .filter(service__project=obj)
                .order_by('-created_at')
                .values('created_at')
                .first()
            )
            return dep['created_at'].isoformat() if dep else None

        @classmethod
        def prefetch_for_list(cls, queryset):
            """Attach bulk-fetched data to avoid N+1 in list views."""
            from django.db.models import Count, Max
            from apps.deployments.models import Deployment
            from apps.deployments.models import Service

            project_ids = list(queryset.values_list('id', flat=True))

            service_counts = dict(
                Service.objects.filter(project_id__in=project_ids)
                .values('project_id')
                .annotate(cnt=Count('id'))
                .values_list('project_id', 'cnt')
            )

            latest_deps = dict(
                Deployment.objects.filter(service__project_id__in=project_ids)
                .values('service__project_id')
                .annotate(latest_status=Max('status'), latest_at=Max('created_at'))
                .values_list('service__project_id', 'latest_status', 'latest_at')
            )

            instance = cls()
            instance._service_counts = service_counts
            instance._latest_statuses = {pid: status for pid, status, _ in latest_deps.items()}
            instance._latest_times = {
                pid: at.isoformat() if at else None
                for pid, _, at in latest_deps.items()
            }
            return instance

    serializer_class = ProjectSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            page_ids = [obj.id for obj in page]
            filtered_qs = queryset.model.objects.filter(id__in=page_ids)
            serializer = self.ProjectSerializer.prefetch_for_list(filtered_qs)
            serializer.instance = page
            serializer.context = {'request': request}
            return self.get_paginated_response(serializer.data)
        serializer = self.ProjectSerializer.prefetch_for_list(queryset)
        serializer.instance = queryset
        serializer.context = {'request': request}
        return Response(serializer.data)

    def get_queryset(self):
        """Owner and Team scoped: users see their own projects and team projects.

        Ephemeral projects (auto-created for custom-registry deploys) are
        hidden from the default listing unless the user is a superuser or
        explicitly requests ``?include_ephemeral=true``.
        """
        qs = Project.objects.all().order_by('id')
        if self.request.user.is_superuser:
            return qs
        qs = qs.filter(
            Q(owner=self.request.user) |
            Q(team__members__user=self.request.user)
        ).distinct().order_by('id')

        # Hide ephemeral projects by default
        include_ephemeral = self.request.query_params.get('include_ephemeral', '').lower() in ('true', '1', 'yes')
        if not include_ephemeral:
            qs = qs.filter(is_ephemeral=False)

        return qs

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
        """
        from django.db.models import Q
        project = self.get_object()
        service_id = request.data.get('service_id')
        if not service_id:
            return Response(
                {'error': 'service_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            service = Service.objects.get(
                Q(owner=request.user) | Q(project__team__members__user=request.user),
                id=service_id
            )
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found or access denied'},
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
        Body: { "service_id": "uuid", "replacement_project_id": "uuid" }

        SECURITY (Issue 50): unlinking a service from its project would
        orphan it — license tier checks and billing assume
        ``service.project`` is non-null. The endpoint therefore requires
        a ``replacement_project_id`` and re-attaches the service to that
        project in the same DB transaction. The original caller's
        project is verified as the service's *current* project, the
        replacement project is verified as accessible to the caller,
        and the service is moved atomically.
        """
        serializer = RemoveServiceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': 'service_id and replacement_project_id are required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service_id = serializer.validated_data['service_id']
        replacement_project_id = serializer.validated_data['replacement_project_id']

        project = self.get_object()

        try:
            with transaction.atomic():
                service = Service.objects.select_for_update().get(
                    Q(owner=request.user)
                    | Q(project__team__members__user=request.user),
                    id=service_id,
                )
                if service.project_id != project.id:
                    return Response(
                        {'error': 'Service is not in this project'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                replacement_qs = Project.objects.filter(id=replacement_project_id)
                if not request.user.is_superuser:
                    replacement_qs = replacement_qs.filter(
                        Q(owner=request.user)
                        | Q(team__members__user=request.user)
                    )
                if not replacement_qs.exists():
                    return Response(
                        {'error': 'Replacement project not found or access denied'},
                        status=status.HTTP_404_NOT_FOUND,
                    )

                service.project_id = replacement_project_id
                service.save(update_fields=['project', 'updated_at'])
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found or access denied'},
                status=status.HTTP_404_NOT_FOUND,
            )

        logger.info(
            'Moved service %s (%s) from project %s (%s) to replacement %s',
            service.name, service.id, project.name, project.id, replacement_project_id,
        )
        return Response({
            'status': 'ok',
            'service_id': str(service.id),
            'project_id': str(project.id),
            'replacement_project_id': str(replacement_project_id),
        })

    @action(detail=True, methods=['get', 'post'], url_path='registry')
    def registry(self, request, pk=None):
        """
        GET  /api/v1/projects/{id}/registry/  — get project's scoped registry
        POST /api/v1/projects/{id}/registry/ — set project's scoped registry

        POST body::
            {
                "registry_url": "my-registry.internal:5000",
                "username": "admin",
                "password": "...",
                "allowed_registry_hosts": ["my-registry.internal:5000"]
            }

        Returns the effective registry config (walks hierarchy if none set).
        """
        from django.contrib.contenttypes.models import ContentType

        from apps.deployments.models.registry_scope import ScopedRegistry
        from ..serializers_registry_scope import (
            ScopedRegistryReadSerializer,
            ScopedRegistrySerializer,
        )

        project = self.get_object()

        if request.method == 'POST':
            data = {**request.data, 'scope_type': 'project', 'scope_id': str(project.id)}
            serializer = ScopedRegistrySerializer(data=data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            ct = ContentType.objects.get_for_model(project)

            # Update or create
            existing = ScopedRegistry.objects.filter(
                content_type=ct, object_id=project.id
            ).first()

            if existing:
                for field, value in serializer.validated_data.items():
                    if field not in ('scope_type', 'scope_id', 'content_type', 'object_id'):
                        # Guard: skip empty password to avoid overwriting stored credentials.
                        # The frontend sends password=undefined when the user leaves the
                        # field blank, but the API should also protect against explicit
                        # empty-string payloads.
                        if field == 'password' and not value:
                            continue
                        setattr(existing, field, value)
                existing.save()
                logger.info("Updated scoped registry for project %s", project.id)
                return Response({'status': 'updated', 'id': str(existing.id)})
            else:
                instance = serializer.save()
                logger.info("Created scoped registry for project %s", project.id)
                return Response({'status': 'created', 'id': str(instance.id)},
                                status=status.HTTP_201_CREATED)

        # GET: return effective registry config (walks hierarchy)
        creds = ScopedRegistry.resolve_registry_credentials(project)
        scoped = ScopedRegistry.get_for_object(project)
        read_ser = ScopedRegistryReadSerializer(scoped) if scoped else None

        return Response({
            'effective_url': creds.get('url', ''),
            'has_username': bool(creds.get('username')),
            'has_password': bool(creds.get('password')),
            'is_scoped': scoped is not None,
            'scoped_config': read_ser.data if read_ser else None,
            'hierarchy': ['project', 'team', 'organization', 'platform'],
        })

    @action(detail=True, methods=['post'], url_path='sync-envs')
    def sync_envs(self, request, pk=None):
        """
        POST /api/v1/projects/{id}/sync-envs/

        Hardens and synchronizes environment variables across all services in the ecosystem.
        Deterministic linking of Intelligence, Security, and Core services.
        """
        project = self.get_object()
        from apps.deployments.services.ecosystem import sync_ecosystem_envs

        try:
            logger.info("Triggering instant ecosystem sync for project %s (%s)", project.name, project.id)
            result = sync_ecosystem_envs(str(project.id))
            return Response(result)
        except Exception as e:
            logger.exception("Ecosystem sync failed for project %s", project.id)
            return Response(
                {"error": f"Sync failed: {e!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    # ── Project Members ───────────────────────────────────────────

    @action(detail=True, methods=['get'], url_path='members')
    def project_members(self, request, pk=None):
        """GET /api/v1/projects/{id}/members/ — list project members."""
        project = self.get_object()
        members = ProjectMember.objects.filter(project=project).select_related('user').order_by('role', 'user__username')

        class ProjectMemberSerializer(drf_serializers.ModelSerializer):
            username = drf_serializers.CharField(source='user.username', read_only=True)
            email = drf_serializers.EmailField(source='user.email', read_only=True)

            class Meta:
                model = ProjectMember
                fields = ('id', 'user', 'username', 'email', 'role', 'permissions', 'expires_at', 'joined_at')
                read_only_fields = ('user', 'joined_at')

        return Response(ProjectMemberSerializer(members, many=True).data)

    @action(detail=True, methods=['post'], url_path='members/invite')
    def invite_project_member(self, request, pk=None):
        """POST /api/v1/projects/{id}/members/invite/ — add a member to this project."""
        project = self.get_object()

        if not has_permission(request.user, project, MEMBER_INVITE):
            return Response({'error': 'You do not have permission to invite project members'}, status=status.HTTP_403_FORBIDDEN)

        email = request.data.get('email', '').strip()
        role = request.data.get('role', ProjectMember.Role.MEMBER)
        expires_at = request.data.get('expires_at')
        permissions_override = request.data.get('permissions')

        User = get_user_model()
        try:
            invitee = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        if ProjectMember.objects.filter(project=project, user=invitee).exists():
            return Response({'error': 'User already a project member'}, status=status.HTTP_400_BAD_REQUEST)

        member = ProjectMember.objects.create(
            project=project,
            user=invitee,
            role=role if role in dict(ProjectMember.Role.choices) else ProjectMember.Role.MEMBER,
            expires_at=expires_at if expires_at else None,
            permissions=permissions_override if isinstance(permissions_override, list) else [],
        )

        class ProjectMemberSerializer(drf_serializers.ModelSerializer):
            username = drf_serializers.CharField(source='user.username', read_only=True)
            email = drf_serializers.EmailField(source='user.email', read_only=True)

            class Meta:
                model = ProjectMember
                fields = ('id', 'user', 'username', 'email', 'role', 'permissions', 'expires_at', 'joined_at')
                read_only_fields = ('user', 'joined_at')

        return Response(ProjectMemberSerializer(member).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path=r'members/(?P<member_id>[^/.]+)/remove')
    def remove_project_member(self, request, pk=None, member_id=None):
        """POST /api/v1/projects/{id}/members/{mid}/remove/"""
        project = self.get_object()

        if not has_permission(request.user, project, MEMBER_REMOVE):
            return Response({'error': 'You do not have permission to remove project members'}, status=status.HTTP_403_FORBIDDEN)

        try:
            member = ProjectMember.objects.get(project=project, id=member_id)
        except ProjectMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

        member.delete()
        return Response({'status': 'removed'})

    @action(detail=True, methods=['post'], url_path=r'members/(?P<member_id>[^/.]+)/change-role')
    def change_project_member_role(self, request, pk=None, member_id=None):
        """POST /api/v1/projects/{id}/members/{mid}/change-role/"""
        project = self.get_object()

        if not has_permission(request.user, project, MEMBER_ROLE_CHANGE):
            return Response({'error': 'You do not have permission to change project member roles'}, status=status.HTTP_403_FORBIDDEN)

        try:
            member = ProjectMember.objects.get(project=project, id=member_id)
        except ProjectMember.DoesNotExist:
            return Response({'error': 'Member not found'}, status=status.HTTP_404_NOT_FOUND)

        new_role = request.data.get('role')
        expires_at = request.data.get('expires_at')
        permissions_override = request.data.get('permissions')

        if new_role and new_role in dict(ProjectMember.Role.choices):
            member.role = new_role
        if expires_at is not None:
            member.expires_at = expires_at if expires_at else None
        if permissions_override is not None:
            member.permissions = permissions_override if isinstance(permissions_override, list) else []

        member.save(update_fields=[f for f in ('role', 'expires_at', 'permissions') if f in request.data])
        return Response({'status': 'updated'})
