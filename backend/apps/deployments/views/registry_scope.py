"""
ViewSet for ScopedRegistry CRUD + resolve endpoint.
"""

import logging

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from ..models import ScopedRegistry
from apps.deployments.serializers.registry_scope import (
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
        qs = ScopedRegistry.objects.all().select_related("content_type").order_by("-created_at", "id")
        user = self.request.user

        # Filter by scope_type + scope_id query params
        scope_type = self.request.query_params.get("scope_type")
        scope_id = self.request.query_params.get("scope_id")
        if scope_type and scope_id:
            ct = ContentType.objects.filter(model=scope_type).first()
            if not ct:
                return qs.none()
            qs = qs.filter(content_type=ct, object_id=scope_id)

        # SECURITY: Scope results to entities the user owns or actively belongs to
        if not user.is_superuser:
            from apps.organizations.models import Organization, OrganizationMembership
            from apps.organizations.models.project import ProjectMember
            from apps.teams.models import Team, TeamMember

            from ..models.core import Project

            now = timezone.now()
            not_expired = Q(expires_at__isnull=True) | Q(expires_at__gt=now)

            active_org_ids = set(
                OrganizationMembership.objects.filter(
                    user=user, is_active=True
                ).filter(not_expired).values_list("organization_id", flat=True)
            )
            own_org_ids = set(
                Organization.objects.filter(owner=user).values_list("id", flat=True)
            )

            active_team_ids = set(
                TeamMember.objects.filter(
                    user=user, is_active=True
                ).filter(not_expired).values_list("team_id", flat=True)
            )
            own_team_ids = set(
                Team.objects.filter(owner=user).values_list("id", flat=True)
            )
            all_team_ids = active_team_ids | own_team_ids

            project_member_ids = set(
                ProjectMember.objects.filter(user=user)
                .filter(not_expired)
                .values_list("project_id", flat=True)
            )
            own_project_ids = set(
                Project.objects.filter(
                    Q(owner=user) | Q(team_id__in=all_team_ids)
                ).values_list("id", flat=True)
            )

            scope_map = {
                "organization": active_org_ids | own_org_ids,
                "team": all_team_ids,
                "project": project_member_ids | own_project_ids,
            }

            scope_filters = Q()
            matched_any = False
            for ct_model, obj_ids in scope_map.items():
                if not obj_ids:
                    continue
                ct = ContentType.objects.filter(model=ct_model).first()
                if not ct:
                    continue
                scope_filters |= Q(content_type=ct, object_id__in=obj_ids)
                matched_any = True

            if not matched_any:
                return qs.none()

            qs = qs.filter(scope_filters)

        return qs

    def _resolve_scope_object(self, scope_type: str, scope_id):
        ct = ContentType.objects.filter(model=scope_type).first()
        if not ct:
            raise ValidationError({"scope_type": f"Unknown scope type: {scope_type}"})
        try:
            return ct.get_object_for_this_type(pk=scope_id)
        except ct.model_class().DoesNotExist:
            raise ValidationError({"scope_id": f"Target {scope_type} not found."})

    def _validate_scope_permission(self, serializer, instance=None):
        user = self.request.user
        if user.is_superuser:
            return

        if instance is not None:
            existing_type = instance.content_type.model if instance.content_type else ""
            existing_obj = instance.scope
            if existing_obj is None or not _user_can_write_scope(user, existing_type, existing_obj):
                raise PermissionDenied("You do not have permission to modify this scoped registry.")

        scope_type = serializer.validated_data.get("scope_type")
        scope_id = serializer.validated_data.get("scope_id")
        if not scope_type or not scope_id:
            if instance is not None:
                return
            raise ValidationError({"scope_type": "scope_type and scope_id are required."})

        obj = self._resolve_scope_object(scope_type, scope_id)
        if not _user_can_write_scope(user, scope_type, obj):
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
        self._validate_scope_permission(serializer, instance=serializer.instance)
        instance = serializer.save()
        logger.info(
            "ScopedRegistry updated: %s (%s) → %s",
            instance.scope,
            instance.content_type.model,
            instance.registry_url,
        )

    def perform_destroy(self, instance):
        user = self.request.user
        if not user.is_superuser:
            scope_type = instance.content_type.model if instance.content_type else ""
            scope_obj = instance.scope
            if scope_obj is None or not _user_can_write_scope(user, scope_type, scope_obj):
                raise PermissionDenied("You do not have permission to delete this scoped registry.")
        instance.delete()

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

        # SECURITY: Verify caller has read access to this scope
        user = request.user
        if not user.is_superuser and not _user_can_read_scope(user, scope_type, obj):
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


def _user_can_read_scope(user, scope_type: str, obj) -> bool:
    """Check if a user owns or actively belongs to the given scope object."""
    if user.is_superuser:
        return True

    now = timezone.now()
    not_expired = Q(expires_at__isnull=True) | Q(expires_at__gt=now)

    if scope_type == "organization":
        from apps.organizations.models import OrganizationMembership
        if obj.owner_id == user.id:
            return True
        return OrganizationMembership.objects.filter(
            organization=obj, user=user, is_active=True
        ).filter(not_expired).exists()

    elif scope_type == "team":
        from apps.teams.models import TeamMember
        if obj.owner_id == user.id:
            return True
        return TeamMember.objects.filter(
            team=obj, user=user, is_active=True
        ).filter(not_expired).exists()

    elif scope_type == "project":
        from apps.organizations.models.project import ProjectMember
        if obj.owner_id == user.id:
            return True
        if ProjectMember.objects.filter(project=obj, user=user).filter(not_expired).exists():
            return True
        if getattr(obj, "team_id", None):
            from apps.teams.models import TeamMember
            if obj.team.owner_id == user.id:
                return True
            return TeamMember.objects.filter(
                team_id=obj.team_id, user=user, is_active=True
            ).filter(not_expired).exists()

    return False


def _user_can_write_scope(user, scope_type: str, obj) -> bool:
    """Check if a user holds owner or admin privileges on the given scope object."""
    if user.is_superuser:
        return True

    now = timezone.now()
    not_expired = Q(expires_at__isnull=True) | Q(expires_at__gt=now)

    if scope_type == "organization":
        from apps.organizations.models import OrganizationMembership
        if obj.owner_id == user.id:
            return True
        return OrganizationMembership.objects.filter(
            organization=obj,
            user=user,
            is_active=True,
            role__in=("OWNER", "ADMIN"),
        ).filter(not_expired).exists()

    elif scope_type == "team":
        from apps.teams.models import TeamMember
        if obj.owner_id == user.id:
            return True
        return TeamMember.objects.filter(
            team=obj,
            user=user,
            is_active=True,
            role="ADMIN",
        ).filter(not_expired).exists()

    elif scope_type == "project":
        from apps.organizations.models.project import ProjectMember
        if obj.owner_id == user.id:
            return True
        if ProjectMember.objects.filter(
            project=obj, user=user, role="ADMIN"
        ).filter(not_expired).exists():
            return True
        if getattr(obj, "team_id", None):
            from apps.teams.models import TeamMember
            if obj.team.owner_id == user.id:
                return True
            return TeamMember.objects.filter(
                team_id=obj.team_id,
                user=user,
                is_active=True,
                role="ADMIN",
            ).filter(not_expired).exists()

    return False


def _user_can_access_scope(user, scope_type: str, obj) -> bool:
    """Backward-compatible alias for read access."""
    return _user_can_read_scope(user, scope_type, obj)
