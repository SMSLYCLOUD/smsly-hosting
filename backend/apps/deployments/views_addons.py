from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models_addons import Addon
from .models import Service, EnvironmentVariable
import logging

logger = logging.getLogger(__name__)


class AddonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Addon
        fields = ['id', 'service', 'name', 'addon_type', 'status', 'connection_url', 'created_at']
        read_only_fields = ['status', 'connection_url', 'created_at']


class AddonViewSet(viewsets.ModelViewSet):
    serializer_class = AddonSerializer
    permission_classes = [IsAuthenticated]

    # ==========================================================================
    # SECURITY: Zero Trust - Only return addons for user's own services
    # ==========================================================================
    def get_queryset(self):
        """Filter addons to only those belonging to the user's services."""
        return Addon.objects.filter(service__owner=self.request.user)

    def perform_create(self, serializer):
        # SECURITY: Verify user owns the service before creating addon
        service = serializer.validated_data.get('service')
        if service and service.owner != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Access denied to this service.")
        
        addon = serializer.save()
        # Trigger async provisioning via Celery (uses Docker-native provisioner)
        from .tasks import provision_addon_task
        provision_addon_task.delay(str(addon.id))

    @action(detail=True, methods=['post'])
    def deprovision(self, request, pk=None):
        """Delete addon container and remove from service."""
        addon = self.get_object()
        from .tasks import deprovision_addon_task
        deprovision_addon_task.delay(str(addon.id))
        return Response({'status': 'deprovisioning'}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def status_check(self, request, pk=None):
        """Check current addon container status."""
        addon = self.get_object()
        
        container_id = addon.coolify_uuid  # We store container_id here
        
        if not container_id:
            return Response({
                'status': addon.status,
                'message': 'Not yet provisioned'
            })
        
        # Check Docker container status
        from services.addon_provisioner import addon_provisioner
        
        try:
            container_status = addon_provisioner.get_status(container_id)
            return Response({
                'status': addon.status,
                'container_running': container_status.get('running', False),
                'container_status': container_status.get('status', 'unknown'),
            })
        except Exception as e:
            logger.error(f"Failed to check addon status: {e}")
            return Response({
                'status': addon.status,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
