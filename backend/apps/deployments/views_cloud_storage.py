"""Cloud storage destinations API — create, list, update, delete, test connection."""
from rest_framework import viewsets, permissions, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models_cloud_storage import CloudStorageDestination


class CloudStorageSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source='get_provider_display', read_only=True)
    secret_key_masked = serializers.SerializerMethodField()

    class Meta:
        model = CloudStorageDestination
        fields = ['id', 'name', 'provider', 'provider_display', 'bucket',
                  'region', 'endpoint', 'access_key', 'secret_key',
                  'secret_key_masked', 'is_active', 'created_at']
        extra_kwargs = {'secret_key': {'write_only': True}}

    def get_secret_key_masked(self, obj):
        key = obj.secret_key or ''
        return key[:4] + '****' + key[-4:] if len(key) > 8 else '****'


class CloudStorageViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CloudStorageSerializer

    def get_queryset(self):
        return CloudStorageDestination.objects.filter(is_active=True)

    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        destination = self.get_object()
        success = destination.upload_test_file()
        if success:
            return Response({'status': 'ok', 'message': 'Test file uploaded successfully'})
        return Response({'status': 'error', 'message': 'Upload failed — check credentials and endpoint'},
                        status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def templates(self, request):
        return Response(CloudStorageDestination.TEMPLATES)
