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
            from django.db.models import Q
            return CronJob.objects.filter(
                Q(service__owner=self.request.user) | Q(service__project__team__members__user=self.request.user),
                service_id=self.kwargs['service_pk']
            ).distinct()
        return CronJob.objects.none()  # Should be nested

    def perform_create(self, serializer):
        service = Service.objects.get(pk=self.kwargs['service_pk'])
        has_access = (
            service.owner == self.request.user or 
            (service.project and service.project.team and service.project.team.members.filter(user=self.request.user).exists())
        )
        if not has_access:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have access to this service.")
        serializer.save(service=service)
