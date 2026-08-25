"""
Base ManagedServerViewSet — CRUD, queryset, serializer, and permissions.
"""

import logging

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from ...models.servers import ManagedServer
from .serializers import (
    ManagedServerCreateSerializer,
    ManagedServerProvisionSerializer,
    ManagedServerSerializer,
)

logger = logging.getLogger(__name__)


class ManagedServerViewSet(viewsets.ModelViewSet):
    queryset = ManagedServer.objects.all()
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        action_attr = getattr(self, self.action, None) if self.action else None
        action_throttles = getattr(action_attr, "throttle_classes", None)
        if action_throttles:
            return [throttle() for throttle in action_throttles]
        return super().get_throttles()

    def get_queryset(self):
        from django.db.models import Q
        from django.utils import timezone
        user = self.request.user
        if user.is_superuser:
            qs = self.queryset.all()
        else:
            qs = self.queryset.filter(
                Q(owner=user) |
                Q(project__team__members__user=user,
                  project__team__members__is_active=True)
            ).distinct()
            # Exclude expired memberships
            qs = qs.exclude(
                project__team__members__user=user,
                project__team__members__expires_at__isnull=False,
                project__team__members__expires_at__lt=timezone.now(),
            )
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def _get_object_for_agent(self, pk):
        try:
            return ManagedServer.objects.get(pk=pk)
        except ManagedServer.DoesNotExist:
            from rest_framework.exceptions import NotFound
            raise NotFound

    def get_serializer_class(self):
        if self.action == "provision_new":
            return ManagedServerProvisionSerializer
        if self.action in ["create", "update", "partial_update"]:
            return ManagedServerCreateSerializer
        return ManagedServerSerializer

    def perform_create(self, serializer):
        server = serializer.save(owner=self.request.user)
        self._start_server_health_sync(server)

    def perform_update(self, serializer):
        server = serializer.save()
        self._start_server_health_sync(server)

    def _start_server_health_sync(self, server):
        has_connection_hint = bool(
            server.api_url
            or server.api_token
            or server.gateway_secret
            or server.ssh_key
            or server.ssh_password
        )
        if server.host and has_connection_hint:
            from threading import Thread
            Thread(target=self._sync_server_health, args=(server.id,), daemon=True).start()

    def _sync_server_health(self, server_id):
        try:
            server = ManagedServer.objects.get(id=server_id)
            from .helpers import _refresh_managed_server_health
            _refresh_managed_server_health(server)
        except Exception as e:
            logger.error(f"Background server sync failed for {server_id}: {e}")
