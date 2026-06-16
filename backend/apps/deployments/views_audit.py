"""Audit log read-only API view.

Extracted from ``apps.deployments.views`` as part of the Phase-1 refactor
(see ``docs/REFACTOR_PLAN_VIEWS_TASKS.md``). ``AuditLogViewSet`` is
re-exported from ``apps.deployments.views`` for backwards compatibility with
``apps.deployments.urls`` and any test that imports it from the parent
module.
"""
from rest_framework import permissions, viewsets

from .models_audit import AuditLog
from .serializers import AuditLogSerializer
from django.db.models import Q


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        """ZH-001 FIX: Filter audit logs to only show entries for the requesting user."""
        if self.request.user.is_superuser:
            qs = AuditLog.objects.all()
        else:
            username = self.request.user.get_username()
            qs = AuditLog.objects.filter(actor=username)

        # Search filter
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(action__icontains=search) |
                Q(actor__icontains=search) |
                Q(target__icontains=search)
            )
        return qs
