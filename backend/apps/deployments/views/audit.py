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
            accessible = list(
                Service.objects.filter(get_accessible_q(user)).values_list("id", "name")
            )
            target_candidates: list[str] = []
            for sid, sname in accessible:
                if sname:
                    target_candidates.extend([sname, f"Service:{sname}", f"Service: {sname}"])
                if sid:
                    uid = str(sid)
                    target_candidates.extend([uid, f"Service:{uid}", f"Service: {uid}"])
            qs = AuditLog.objects.filter(
                Q(actor=username) | Q(target__in=target_candidates)
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
