"""Views for platform self-update management."""
from rest_framework import permissions, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models.updates import PlatformUpdate


class PlatformUpdateSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = PlatformUpdate
        fields = [
            'id', 'status', 'from_version', 'to_version',
            'from_commit', 'to_commit', 'progress_percent',
            'current_step', 'logs', 'error_message',
            'can_rollback', 'rollback_deadline',
            'created_at', 'completed_at', 'initiated_by',
        ]
        read_only_fields = fields


class PlatformUpdateViewSet(viewsets.ReadOnlyModelViewSet):
    """List and view platform updates. Admin-only trigger for update/rollback."""
    queryset = PlatformUpdate.objects.all()
    serializer_class = PlatformUpdateSerializer
    permission_classes = [permissions.IsAdminUser]

    @action(detail=False, methods=['post'])
    def trigger(self, request):
        """Trigger a platform update."""
        # Check no update is already in progress
        in_progress = PlatformUpdate.objects.filter(
            status__in=['PENDING', 'PULLING', 'BACKING_UP',
                       'MIGRATING', 'RESTARTING', 'HEALTH_CHECK']
        ).exists()
        if in_progress:
            return Response(
                {'error': 'An update is already in progress'},
                status=status.HTTP_409_CONFLICT)

        update = PlatformUpdate.objects.create(initiated_by='api')

        # Run async
        from apps.deployments.tasks.infra.tasks_platform_update import platform_update_task
        platform_update_task.delay(update_id=str(update.id))

        return Response(
            PlatformUpdateSerializer(update).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def rollback(self, request, pk=None):
        """Manually trigger rollback for a completed update."""
        update = self.get_object()
        if not update.can_rollback:
            return Response(
                {'error': 'Rollback not available'},
                status=status.HTTP_400_BAD_REQUEST)

        from apps.deployments.tasks.infra.tasks_platform_update import platform_rollback_task
        platform_rollback_task.delay(update_id=str(update.id))
        update.status = 'ROLLING_BACK'
        update.save(update_fields=['status'])
        return Response(PlatformUpdateSerializer(update).data)
