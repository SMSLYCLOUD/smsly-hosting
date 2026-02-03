"""Views Cron module."""
from rest_framework import viewsets, permissions, serializers
from .models_cron import CronJob
from .models import Service


class CronJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = CronJob
        fields = '__all__'
        read_only_fields = [
            'id',
            'created_at',
            'updated_at',
            'last_run_at',
            'next_run_at']


class CronJobViewSet(viewsets.ModelViewSet):
    serializer_class = CronJobSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Filter by service if provided in query params or nested
        # /api/v1/services/{id}/cron/
        if 'service_pk' in self.kwargs:
            return CronJob.objects.filter(service_id=self.kwargs['service_pk'])
        return CronJob.objects.none()  # Should be nested

    def perform_create(self, serializer):
        service = Service.objects.get(pk=self.kwargs['service_pk'])
        serializer.save(service=service)
