"""
Views for SMSLY development tunnels.

Provides endpoints for the TunnelDashboard frontend component:
  - GET  /api/v1/tunnels/              → list active tunnels
  - GET  /api/v1/tunnels/{id}/requests/ → list captured requests
  - POST /api/v1/tunnels/{id}/replay/{req_id}/ → replay a request
  - POST /api/v1/tunnels/register/     → register a new tunnel (CLI)
"""
from rest_framework import viewsets, serializers, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.licensing.decorators import require_tier
from .models_tunnels import Tunnel, TunnelRequest
import logging

logger = logging.getLogger(__name__)


class TunnelRequestSerializer(serializers.ModelSerializer):
    responseTimeMs = serializers.IntegerField(
        source='response_time_ms', read_only=True)

    class Meta:
        model = TunnelRequest
        fields = [
            'id', 'method', 'path', 'status',
            'responseTimeMs', 'timestamp']


class TunnelSerializer(serializers.ModelSerializer):
    tunnelId = serializers.UUIDField(source='id', read_only=True)
    publicUrl = serializers.URLField(source='public_url', read_only=True)
    localPort = serializers.IntegerField(source='local_port', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    requestCount = serializers.IntegerField(
        source='request_count', read_only=True)
    isActive = serializers.BooleanField(source='is_active', read_only=True)

    class Meta:
        model = Tunnel
        fields = [
            'tunnelId', 'subdomain', 'publicUrl',
            'localPort', 'createdAt', 'requestCount', 'isActive']


class TunnelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing development tunnels.

    SECURITY: Zero Trust — users can only see their own tunnels.
    """
    queryset = Tunnel.objects.all()
    serializer_class = TunnelSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(
            owner=self.request.user, is_active=True)

    @require_tier('pro', 'enterprise')
    def list(self, request):
        """GET /api/v1/tunnels/ — returns {tunnels: [...]}"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({'tunnels': serializer.data})

    @action(detail=True, methods=['get'], url_path='requests')
    @require_tier('pro', 'enterprise')
    def get_requests(self, request, pk=None):
        """GET /api/v1/tunnels/{id}/requests/ — returns {requests: [...]}"""
        tunnel = self.get_object()
        tunnel_requests = tunnel.requests.all()[:100]
        serializer = TunnelRequestSerializer(tunnel_requests, many=True)
        return Response({'requests': serializer.data})

    @action(
        detail=True,
        methods=['post'],
        url_path=r'replay/(?P<request_id>[0-9a-f-]{36})',
    )
    @require_tier('pro', 'enterprise')
    def replay(self, request, pk=None, request_id=None):
        """POST /api/v1/tunnels/{id}/replay/{req_id}/ — replay a request"""
        tunnel = self.get_object()
        try:
            tunnel_req = tunnel.requests.get(id=request_id)
        except TunnelRequest.DoesNotExist:
            return Response(
                {'error': 'Request not found'},
                status=status.HTTP_404_NOT_FOUND)

        # Create a copy of the request for replay
        replayed = TunnelRequest.objects.create(
            tunnel=tunnel,
            method=tunnel_req.method,
            path=tunnel_req.path,
            headers=tunnel_req.headers,
            body_preview=tunnel_req.body_preview,
        )
        logger.info(
            f"Replayed request {request_id} as {replayed.id} "
            f"for tunnel {tunnel.subdomain}")
        return Response(
            {'status': 'replayed', 'new_request_id': str(replayed.id)},
            status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'])
    @require_tier('pro', 'enterprise')
    def register(self, request):
        """POST /api/v1/tunnels/register/ — register tunnel from CLI tool"""
        subdomain = request.data.get('subdomain')
        local_port = request.data.get('local_port')

        if not subdomain or not local_port:
            return Response(
                {'error': 'subdomain and local_port required'},
                status=status.HTTP_400_BAD_REQUEST)

        tunnel, created = Tunnel.objects.update_or_create(
            subdomain=subdomain,
            owner=request.user,
            defaults={
                'local_port': local_port,
                'public_url': f"https://{subdomain}.tunnel.smsly.cloud",
                'is_active': True,
            })

        return Response(
            TunnelSerializer(tunnel).data,
            status=status.HTTP_201_CREATED if created
            else status.HTTP_200_OK)
