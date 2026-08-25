"""Audit log read-only API view.

Extracted from ``apps.deployments.views`` as part of the Phase-1 refactor
(see ``docs/REFACTOR_PLAN_VIEWS_TASKS.md``). ``AuditLogViewSet`` is
re-exported from ``apps.deployments.views`` for backwards compatibility with
``apps.deployments.urls`` and any test that imports it from the parent
module.
"""
from django.db.models import Q
from rest_framework import permissions, viewsets

from ..models.audit import AuditLog
from ..serializers import AuditLogSerializer


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            qs = AuditLog.objects.all()
        else:
            username = user.get_username()
            # Include team/project services via get_accessible_q so members
            # can audit team resources — previously only owner services were
            # visible, hiding admin interventions on team services.
            from apps.deployments.models import Service
            from apps.permissions.utils import get_accessible_q
            accessible_names = list(
                Service.objects.filter(get_accessible_q(user)).values_list("name", flat=True)
            )
            accessible_ids = list(
                Service.objects.filter(get_accessible_q(user)).values_list("id", flat=True)
            )
            qs = AuditLog.objects.filter(
                Q(actor=username) |
                Q(target__in=accessible_names) |
                Q(target__in=[str(uid) for uid in accessible_ids])
            )

        # Search filter
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(action__icontains=search) |
                Q(actor__icontains=search) |
                Q(target__icontains=search)
            )
        return qs
