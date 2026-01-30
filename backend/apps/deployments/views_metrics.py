from rest_framework import serializers, viewsets, mixins
from rest_framework.permissions import IsAuthenticated
from .models_metrics import ServiceMetric
from .models import Service

class ServiceMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceMetric
        fields = ['cpu_usage', 'memory_usage', 'timestamp']

class MetricsViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ServiceMetricSerializer
    permission_classes = [IsAuthenticated]  # SECURITY: Require authentication

    # ==========================================================================
    # SECURITY: Zero Trust - Only return metrics for user's own services
    # ==========================================================================
    def get_queryset(self):
        service_id = self.request.query_params.get('service_id')
        if not service_id:
            return ServiceMetric.objects.none()
        
        # SECURITY: Verify user owns the service before returning metrics
        if not Service.objects.filter(id=service_id, owner=self.request.user).exists():
            return ServiceMetric.objects.none()
        
        return ServiceMetric.objects.filter(service_id=service_id).order_by('timestamp')[:50]
