"""Views Cron module."""
from rest_framework import viewsets, permissions, serializers
from django.db.models import Q
from .models_cron import CronJob
from .models import Service
from .rate_limiting import CronJobCreateRateThrottle
from apps.teams.permissions import get_team_q_filter, assert_can_write, assert_can_delete


class CronJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = CronJob
        fields = '__all__'
        read_only_fields = [
            'id',
            'created_at',
            'updated_at',
            'last_run_at',
            'next_run_at',
            'service']


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
