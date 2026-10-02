"""Cloud storage destinations API — create, list, update, delete, test connection."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.cloud.models.cloud_storage import CloudStorageDestination


class CloudStorageTestRateThrottle(UserRateThrottle):
    """Per-user throttle on the ``test`` endpoint.

    Each test triggers an actual S3 upload.  Cap at 10/minute per user
    to prevent abuse while allowing interactive troubleshooting.
    """
    scope = 'cloud_test'


class CloudStorageTemplatesRateThrottle(UserRateThrottle):
    """Per-user throttle on the ``templates`` convenience endpoint.

    The endpoint returns the static TEMPLATES list — a no-DB
    response — but a script can probe it indefinitely.  The
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

        from apps.cloud.models.backup import validate_endpoint_url
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
            from apps.cloud.models.backup import validate_endpoint_url
            validate_endpoint_url(destination.endpoint)
        except (ValueError, ValidationError):
            return Response(
                {'status': 'error', 'message': 'Endpoint URL is not allowed — check for SSRF risks'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        success = destination.upload_test_file()
        if success:
            return Response({'status': 'ok', 'message': 'Test file uploaded successfully'})
        server_id = (request.query_params.get("server") or request.data.get("server") or "").strip() if hasattr(request, "data") else ""
        if server_id:
            node_result = self._test_from_node(destination, server_id)
            if node_result is not None:
                return node_result
        return Response({'status': 'error', 'message': 'Upload failed — check credentials and endpoint'},
                        status=status.HTTP_400_BAD_REQUEST)

    def _test_from_node(self, destination, server_id):
        from apps.deployments.models.servers import ManagedServer
        from apps.deployments.services.remote_orchestrator import RemoteOrchestrator

        try:
            server = ManagedServer.objects.filter(id=server_id).first()
            if server is None or server.is_primary:
                return None
            # Credentials travel only over the authenticated orchestrator
            # channel (token/HMAC + TLS verify). Never log this payload;
            # node side uses a transient object and scrubbed errors.
            result = RemoteOrchestrator(server).test_remote_storage({
                "provider": destination.provider,
                "bucket": destination.bucket,
                "region": destination.region,
                "endpoint": destination.endpoint,
                "access_key": destination.access_key,
                "secret_key": destination.secret_key,
            })
            if result is None:
                return Response(
                    {'status': 'error', 'message': f'Node {server.name} did not respond.'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            if result.get("error"):
                import re as _re
                safe = _re.sub(
                    r"(?i)((?:authorization|api[_-]?key|token|secret|password|access[_-]?key)\s*[:=]\s*)[^\s,;}{]+",
                    r"\1***",
                    str(result["error"])[:200],
                )
                return Response(
                    {'status': 'error', 'message': f"Node upload failed: {safe}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({'status': 'ok', 'message': f'Test file uploaded successfully from node {server.name}.'})
        except Exception as exc:
            import re as _re2
            safe = _re2.sub(
                r"(?i)((?:authorization|api[_-]?key|token|secret|password|access[_-]?key)\s*[:=]\s*)[^\s,;}{]+",
                r"\1***",
                str(exc)[:200],
            )
            return Response(
                {'status': 'error', 'message': f'Node test failed: {safe}'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

    @action(detail=False, methods=['get'],
            throttle_classes=[CloudStorageTemplatesRateThrottle])
    def templates(self, request):
        return Response(CloudStorageDestination.TEMPLATES)
