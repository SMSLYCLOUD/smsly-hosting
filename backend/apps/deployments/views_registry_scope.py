"""
ViewSet for ScopedRegistry CRUD + resolve endpoint.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import ScopedRegistry
from .serializers_registry_scope import (
    ScopedRegistryReadSerializer,
    ScopedRegistrySerializer,
)

logger = logging.getLogger(__name__)


class ScopedRegistryViewSet(viewsets.ModelViewSet):
    """
    Manage container registry configuration at Organization, Team,
    and Project scopes.

    **List / Create**::

        GET  /api/v1/registry-scopes/
        POST /api/v1/registry-scopes/
              Body: {
                  "scope_type": "project",
                  "scope_id": "uuid",
                  "registry_url": "my-registry.internal:5000",
                  "username": "admin",
                  "password": "..."
              }

    Filter by scope: ``/api/v1/registry-scopes/?scope_type=project&scope_id=<uuid>``

    **Resolve** (walks hierarchy)::

        GET /api/v1/registry-scopes/resolve/?scope_type=project&scope_id=<uuid>

    Returns the effective registry config by walking:
    Project → Team → Organization → PlatformConfig (default)
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list" or self.action == "resolve":
            return ScopedRegistryReadSerializer
        return ScopedRegistrySerializer

    def get_queryset(self):
        qs = ScopedRegistry.objects.all().select_related("content_type")
        user = self.request.user

        # Filter by scope_type + scope_id query params
        scope_type = self.request.query_params.get("scope_type")
        scope_id = self.request.query_params.get("scope_id")
        if scope_type and scope_id:
            ct = ContentType.objects.filter(model=scope_type).first()
            if ct:
                qs = qs.filter(content_type=ct, object_id=scope_id)

        # SECURITY: Scope results to entities the user owns or belongs to
        if not user.is_superuser:
            from django.db.models import Q
            from organizations.models import OrganizationMembership
            from teams.models import TeamMember

            from .models import Organization, Project, Team

            # Resolve content-type-specific Q filters for each scope type
            scope_filters = Q()
            for ct_model in ("organization", "team", "project"):
                ct = ContentType.objects.filter(model=ct_model).first()
                if not ct:
                    continue

                if ct_model == "organization":
                    org_ids = OrganizationMembership.objects.filter(
                        user=user
                    ).values_list("organization_id", flat=True)
                    own_ids = Organization.objects.filter(
                        owner=user
                    ).values_list("id", flat=True)
                    obj_ids = set(org_ids) | set(own_ids)
                elif ct_model == "team":
                    team_ids = TeamMember.objects.filter(
                        user=user, is_active=True
                    ).values_list("team_id", flat=True)
                    own_ids = Team.objects.filter(
                        owner=user
                    ).values_list("id", flat=True)
                    obj_ids = set(team_ids) | set(own_ids)
                elif ct_model == "project":
                    from .models_project import ProjectMember
                    project_ids = ProjectMember.objects.filter(
                        user=user
                    ).values_list("project_id", flat=True)
                    own_ids = Project.objects.filter(
                        owner=user
                    ).values_list("id", flat=True)
                    obj_ids = set(project_ids) | set(own_ids)
                else:
                    continue

                if obj_ids:
                    scope_filters |= Q(content_type=ct, object_id__in=obj_ids)

            qs = qs.filter(scope_filters)

        return qs

    def _validate_scope_permission(self, serializer):
        user = self.request.user
        if user.is_superuser:
            return
        scope_type = serializer.validated_data.get("scope_type")
        scope_id = serializer.validated_data.get("scope_id")
        if not scope_type or not scope_id:
            if self.action in ("update", "partial_update") and self.get_object():
                return
            return
        ct = ContentType.objects.filter(model=scope_type).first()
        if not ct:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"scope_type": f"Unknown scope type: {scope_type}"})
        try:
            obj = ct.get_object_for_this_type(pk=scope_id)
        except ct.model_class().DoesNotExist:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"scope_id": f"Target {scope_type} not found."})
        if not _user_can_access_scope(user, scope_type, obj):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to attach a registry to this scope.")

    def perform_create(self, serializer):
        self._validate_scope_permission(serializer)
        instance = serializer.save()
        logger.info(
            "ScopedRegistry created: %s (%s) → %s",
            instance.scope,
            instance.content_type.model,
            instance.registry_url,
        )

    def perform_update(self, serializer):
        self._validate_scope_permission(serializer)
        instance = serializer.save()
        logger.info(
            "ScopedRegistry updated: %s (%s) → %s",
            instance.scope,
            instance.content_type.model,
            instance.registry_url,
        )

    # ── Resolve endpoint ──────────────────────────────────────────

    @action(detail=False, methods=["get"], url_path="resolve")
    def resolve(self, request):
        """
        GET /api/v1/registry-scopes/resolve/?scope_type=project&scope_id=<uuid>

        Returns the effective registry config for the given scope by
        walking the hierarchy chain.  Falls back to the platform default
        (PlatformConfig) when nothing is configured.
        """
        scope_type = request.query_params.get("scope_type")
        scope_id = request.query_params.get("scope_id")

        if not scope_type or not scope_id:
            return Response(
                {"error": "scope_type and scope_id query params are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ct = ContentType.objects.filter(model=scope_type).first()
        if not ct:
            return Response(
                {"error": f"Unknown scope_type: {scope_type}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            obj = ct.get_object_for_this_type(pk=scope_id)
        except ct.model_class().DoesNotExist:
            return Response(
                {"error": f"{scope_type} with id {scope_id} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # SECURITY: Verify caller has access to this scope
        user = request.user
        if not user.is_superuser and not _user_can_access_scope(user, scope_type, obj):
            return Response(
                {"error": f"{scope_type} with id {scope_id} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        creds = ScopedRegistry.resolve_registry_credentials(obj)
        scoped = ScopedRegistry.get_for_object(obj)

        return Response(
            {
                "effective_url": creds.get("url", ""),
                "has_username": bool(creds.get("username")),
                "has_password": bool(creds.get("password")),
                "is_scoped": scoped is not None,
                "scoped_registry_id": str(scoped.id) if scoped else None,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "is_default": scoped is None,
            }
        )


def _user_can_access_scope(user, scope_type: str, obj) -> bool:
    """Check if a user owns or belongs to the given scope object."""
    if user.is_superuser:
        return True

    if scope_type == "organization":
        from organizations.models import OrganizationMembership
        if obj.owner == user:
            return True
        return OrganizationMembership.objects.filter(
            organization=obj, user=user
        ).exists()

    elif scope_type == "team":
        from teams.models import TeamMember
        if obj.owner == user:
            return True
        return TeamMember.objects.filter(
            team=obj, user=user, is_active=True
        ).exists()

    elif scope_type == "project":
        from .models_project import ProjectMember
        if obj.owner == user:
            return True
        return ProjectMember.objects.filter(
            project=obj, user=user
        ).exists()

    return False
