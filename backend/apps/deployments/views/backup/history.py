"""history mixin."""
import logging

from apps.teams.permissions import get_team_q_filter

from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)


from ...models.backup import ServiceBackup
from ...models.core import Deployment


class HistoryActionsMixin:
    """HistoryActions actions for the viewset."""

    @action(detail=False, methods=['get'], url_path='restore-history')
    def restore_history(self, request):
        """GET /api/v1/backups/restore-history/

        Returns backups that were used in a restore (have restore
        metadata in error_message), plus their associated deployment
        status.  Useful for showing a "Restoration Activity" timeline.
        """
        backups = list(
            ServiceBackup.objects.filter(
                get_team_q_filter(request.user, prefix='service__', request=request)
            ).filter(
                # Restore-related backups have specific markers in error_message
                error_message__icontains='restored'
            ).select_related('service').order_by('-created_at')[:20]
        )

        dep_map: dict[str, list] = {}
        if backups:
            earliest_created = backups[-1].created_at
            service_ids = {b.service_id for b in backups if b.service_id}
            deps = Deployment.objects.filter(
                service_id__in=service_ids,
                created_at__gte=earliest_created,
            ).order_by('created_at')
            for dep in deps:
                sid = str(dep.service_id)
                dep_map.setdefault(sid, []).append(dep)

        from bisect import bisect_left

        results = []
        for b in backups:
            deployment = None
            if b.service_id:
                sid = str(b.service_id)
                dep_list = dep_map.get(sid, [])
                idx = bisect_left(dep_list, b.created_at, key=lambda d: d.created_at)
                if idx < len(dep_list):
                    deployment = dep_list[idx]

            results.append({
                'backup_id': str(b.id),
                'service_id': str(b.service_id) if b.service_id else None,
                'service_name': b.service.name if b.service else None,
                'restored_at': b.created_at.isoformat() if b.created_at else None,
                'restore_type': b.error_message or 'Unknown',
                'deployment_status': deployment.status if deployment else None,
                'deployment_id': str(deployment.id) if deployment else None,
            })
        return Response(results)
