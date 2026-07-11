"""Cloud storage destinations API — create, list, update, delete, test connection."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.deployments.models_cloud_storage import CloudStorageDestination


class CloudStorageTestRateThrottle(UserRateThrottle):
    """Per-user throttle on the ``test`` endpoint.

    Each test triggers an actual S3 upload.  Cap at 10/minute per user
    to prevent abuse while allowing interactive troubleshooting.
    """
    scope = 'cloud_test'


class CloudStorageTemplatesRateThrottle(UserRateThrottle):
    """Per-user throttle on the ``templates`` convenience endpoint.

    The endpoint returns the static TEMPLATES list — a no-DB
    response — but a script can probe it indefinitely. The
    ``cloud-templates`` scope caps it at 30/minute per user.
    Rate is read from
    ``settings.DEFAULT_THROTTLE_RATES['cloud_templates']``.
    """
    scope = 'cloud_templates'


class CloudStorageSerializer(serializers.ModelSerializer):
    provider_display = serializers.CharField(source='get_provider_display', read_only=True)
    secret_key_masked = serializers.SerializerMethodField()
    service_name = serializers.CharField(source='service.name', read_only=True, default=None)

    class Meta:
        model = CloudStorageDestination
        fields = ['id', 'name', 'provider', 'provider_display', 'bucket',
                  'region', 'endpoint', 'access_key', 'secret_key',
                  'secret_key_masked', 'is_active', 'created_at',
                  'service', 'service_name']
        extra_kwargs = {'secret_key': {'write_only': True}}

    def get_secret_key_masked(self, obj):
        key = obj.secret_key or ''
        return key[:4] + '****' + key[-4:] if len(key) > 8 else '****'

    def validate_endpoint(self, value):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from .models_backup import validate_endpoint_url
        try:
            validate_endpoint_url(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value


class CloudStorageViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CloudStorageSerializer

    def get_queryset(self):
        user = self.request.user
        service_id = self.request.GET.get('service')
        platform_only = self.request.GET.get('platform') == 'true'
        show_all = self.request.GET.get('show_all') == 'true' and user.is_superuser

        if show_all:
            # Settings page superuser override: see every destination
            qs = CloudStorageDestination.objects.all()
        else:
            # SECURITY: scope to destinations whose service is owned by the caller,
            # or are platform-wide (service IS NULL). Without this, any authenticated
            # user could list/modify/delete every other user's destinations.
            qs = CloudStorageDestination.objects.filter(
                models.Q(service__owner=user) | models.Q(service__isnull=True)
            ).distinct()

        if service_id and not show_all:
            # Return both platform-wide AND this service's own destinations
            qs = qs.filter(
                models.Q(service__isnull=True) | models.Q(service_id=service_id)
            )
        elif platform_only and not show_all:
            qs = qs.filter(service__isnull=True)
        return qs.filter(is_active=True)

    def perform_create(self, serializer):
        service = serializer.validated_data.get('service')
        if service is None and not self.request.user.is_superuser:
            raise PermissionDenied("Only superusers can create platform-wide cloud storage destinations. Please specify a service.")
        if service and service.owner_id != self.request.user.id:
            raise PermissionDenied("You do not own that service.")
        serializer.save()

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.service is None and not self.request.user.is_superuser:
            raise PermissionDenied("Only superusers can modify platform-wide cloud storage destinations.")
        service = serializer.validated_data.get('service', instance.service)
        if service is None and not self.request.user.is_superuser:
            raise PermissionDenied("Only superusers can convert a destination to platform-wide.")
        if service and service.owner_id != self.request.user.id:
            raise PermissionDenied("You do not own that service.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.service is None and not self.request.user.is_superuser:
            raise PermissionDenied("Only superusers can delete platform-wide cloud storage destinations.")
        super().perform_destroy(instance)

    @action(detail=True, methods=['post'],
            throttle_classes=[CloudStorageTestRateThrottle])
    def test(self, request, pk=None):
        destination = self.get_object()
        # Defense-in-depth: validate endpoint before attempting upload.
        # The serializer's validate_endpoint covers create/update, but the
        # test action bypasses the serializer.  A malicious endpoint that
        # slipped into the DB via ORM or a migration bug would otherwise
        # be tried blindly.
        try:
            from .models_backup import validate_endpoint_url
            validate_endpoint_url(destination.endpoint)
        except (ValueError, ValidationError):
            return Response(
                {'status': 'error', 'message': 'Endpoint URL is not allowed — check for SSRF risks'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        success = destination.upload_test_file()
        if success:
            return Response({'status': 'ok', 'message': 'Test file uploaded successfully'})
        return Response({'status': 'error', 'message': 'Upload failed — check credentials and endpoint'},
                        status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'],
            throttle_classes=[CloudStorageTemplatesRateThrottle])
    def templates(self, request):
        return Response(CloudStorageDestination.TEMPLATES)
