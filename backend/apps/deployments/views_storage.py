from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from .models_storage import Volume

class VolumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Volume
        fields = '__all__'

class VolumeViewSet(viewsets.ModelViewSet):
    serializer_class = VolumeSerializer
    permission_classes = [IsAuthenticated]

    # ==========================================================================
    # SECURITY: Zero Trust - Only return volumes for user's own services
    # ==========================================================================
    def get_queryset(self):
        """Filter volumes to only those belonging to the user's services."""
        return Volume.objects.filter(service__owner=self.request.user)

    def perform_create(self, serializer):
        # SECURITY: Verify user owns the service before creating volume
        service = serializer.validated_data.get('service')
        if service and service.owner != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Access denied to this service.")
        
        vol = serializer.save()
        # In prod: ClusterManager.create_pvc(vol)
