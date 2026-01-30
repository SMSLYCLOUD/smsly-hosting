from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from .models_cron import CronJob

class CronJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = CronJob
        fields = '__all__'

class CronJobViewSet(viewsets.ModelViewSet):
    serializer_class = CronJobSerializer
    permission_classes = [IsAuthenticated]

    # ==========================================================================
    # SECURITY: Zero Trust - Only return cron jobs for user's own services
    # ==========================================================================
    def get_queryset(self):
        """Filter cron jobs to only those belonging to the user's services."""
        return CronJob.objects.filter(service__owner=self.request.user)

    def perform_create(self, serializer):
        # SECURITY: Verify user owns the service before creating cron job
        service = serializer.validated_data.get('service')
        if service and service.owner != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Access denied to this service.")
        
        cron = serializer.save()
        # In prod: ClusterManager.create_cronjob(cron)
