"""history mixin."""
import logging

from apps.teams.permissions import get_team_q_filter

from rest_framework.decorators import action
from rest_framework.response import Response

logger = logging.getLogger(__name__)


from ...models.backup import ServiceBackup


class HistoryActionsMixin:
    """HistoryActions actions for the viewset."""

    @action(detail=False, methods=['get'], url_path='restore-history')
    def restore_history(self, request):
        """GET /api/v1/backups/restore-history/

        Returns backups that were used in a restore (have restore
        metadata in error_message), plus their associated deployment
        status.  Useful for showing a "Restoration Activity" timeline.
        """
        qs = ServiceBackup.objects.filter(
            get_team_q_filter(request.user, prefix='service__', request=request)
        ).filter(
            # Restore-related backups have specific markers in error_message
            error_message__icontains='restored'
        ).order_by('-created_at')[:20]

        results = []
        for b in qs:
            deployment = b.service.deployments.filter(
                created_at__gte=b.created_at
            ).order_by('created_at').first() if b.service_id else None

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
