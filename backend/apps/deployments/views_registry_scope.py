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

        # Filter by scope_type + scope_id query params
        scope_type = self.request.query_params.get("scope_type")
        scope_id = self.request.query_params.get("scope_id")
        if scope_type and scope_id:
            ct = ContentType.objects.filter(model=scope_type).first()
            if ct:
                qs = qs.filter(content_type=ct, object_id=scope_id)

        return qs

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info(
            "ScopedRegistry created: %s (%s) → %s",
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
