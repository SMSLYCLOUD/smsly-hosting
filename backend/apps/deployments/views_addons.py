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
        fields = ['id', 'service', 'name', 'addon_type', 'status', 'connection_url', 
                  'coolify_uuid', 'created_at']
        read_only_fields = ['status', 'connection_url', 'coolify_uuid', 'created_at']


class AddonViewSet(viewsets.ModelViewSet):
    queryset = Addon.objects.all()
    serializer_class = AddonSerializer
    permission_classes = [IsAuthenticated]

    # Map internal addon types to Coolify database types
    ADDON_TYPE_MAP = {
        Addon.Type.POSTGRES: 'postgresql',
        Addon.Type.REDIS: 'redis',
        Addon.Type.MYSQL: 'mysql',
        Addon.Type.MONGODB: 'mongodb',
    }
    
    # Environment variable key mapping
    ENV_KEY_MAP = {
        Addon.Type.POSTGRES: 'DATABASE_URL',
        Addon.Type.REDIS: 'REDIS_URL',
        Addon.Type.MYSQL: 'MYSQL_URL',
        Addon.Type.MONGODB: 'MONGODB_URI',
    }

    def perform_create(self, serializer):
        addon = serializer.save()
        # Trigger async provisioning via Celery
        from .tasks import provision_addon_task
        provision_addon_task.delay(str(addon.id))

    @action(detail=True, methods=['post'])
    def deprovision(self, request, pk=None):
        """Delete addon and its Coolify database."""
        addon = self.get_object()
        from .tasks import deprovision_addon_task
        deprovision_addon_task.delay(str(addon.id))
        return Response({'status': 'deprovisioning'}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def status_check(self, request, pk=None):
        """Check current addon status from Coolify."""
        addon = self.get_object()
        
        if not addon.coolify_uuid:
            return Response({
                'status': addon.status,
                'message': 'Not yet provisioned in Coolify'
            })
        
        # Fetch real status from Coolify
        from services.coolify_client import coolify_client
        import asyncio
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                db_info = loop.run_until_complete(
                    coolify_client.get_database(addon.coolify_uuid)
                )
                return Response({
                    'status': addon.status,
                    'coolify_status': db_info.get('status'),
                    'connection_url': addon.connection_url if not addon.connection_url else '***',
                })
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"Failed to check addon status: {e}")
            return Response({
                'status': addon.status,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

