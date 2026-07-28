"""Views Cron module."""
from django.db.models import Q
from rest_framework import permissions, serializers, viewsets

from apps.teams.permissions import (
    assert_can_delete,
    assert_can_write,
    get_team_q_filter,
)

from ..models import Service  # type: ignore[attr-defined]
from ..models.cron import CronJob
from apps.core.rate_limiting import CronJobCreateRateThrottle


class CronJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = CronJob
        fields = [
            'id', 'service', 'name', 'schedule', 'command',
            'is_active', 'cloud_destination',
            'last_run_at', 'next_run_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'service', 'created_at', 'updated_at',
            'last_run_at', 'next_run_at',
        ]

    def validate_schedule(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("schedule is required.")
        parts = value.strip().split()
        if len(parts) not in (5, 6):
            raise serializers.ValidationError(
                "schedule must be 5 or 6 fields (e.g. '*/5 * * * *')."
            )
        return value

    def validate_command(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("command is required.")
        return value

    def update(self, instance, validated_data):
        # Reset next_run_at if the schedule changes so it gets recalculated immediately
        if 'schedule' in validated_data and validated_data['schedule'] != instance.schedule:
            instance.next_run_at = None
            # Do not need to save here, super().update will save the instance
        return super().update(instance, validated_data)


class CronJobViewSet(viewsets.ModelViewSet):
    serializer_class = CronJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Filter by service if provided in query params or nested
        # /api/v1/services/{id}/cron/
        if 'service_pk' in self.kwargs:
            allowed_services = Service.objects.filter(get_team_q_filter(self.request.user))
            return CronJob.objects.filter(
                Q(service__in=allowed_services),
                service_id=self.kwargs['service_pk']
            ).distinct().order_by("id")
        return CronJob.objects.none()  # Should be nested

    def get_throttles(self):
        if self.action == 'create':
            return [CronJobCreateRateThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        service = Service.objects.get(pk=self.kwargs['service_pk'])
        assert_can_write(self.request.user, service, action='create cron job')
        serializer.save(service=service)

    def perform_update(self, serializer):
        assert_can_write(self.request.user, serializer.instance.service, action='update cron job')
        serializer.save()

    def perform_destroy(self, instance):
        assert_can_delete(self.request.user, instance.service)
        instance.delete()
