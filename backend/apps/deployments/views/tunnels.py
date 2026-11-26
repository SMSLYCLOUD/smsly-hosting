"""
Views for SMSLY development tunnels.

Provides endpoints for the TunnelDashboard frontend component:
  - GET  /api/v1/tunnels/              → list active tunnels
  - POST /api/v1/tunnels/              → create a tunnel from UI
  - DELETE /api/v1/tunnels/{id}/       → soft-delete (deactivate)
  - GET  /api/v1/tunnels/{id}/requests/ → list captured requests
  - POST /api/v1/tunnels/{id}/replay/{req_id}/ → replay a request
  - POST /api/v1/tunnels/{id}/share/   → share tunnel with user
  - POST /api/v1/tunnels/register/     → register a new tunnel (CLI)
"""
import logging
import uuid

from django.conf import settings
from django.db import IntegrityError
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from ..models.tunnels import ReservedSubdomain, Tunnel, TunnelRequest
from .subdomains import RESERVED_LABELS

logger = logging.getLogger(__name__)


# Ports that users are allowed to use for tunnels.
ALLOWED_TUNNEL_PORTS = set(range(1024, 10000)) | set(range(20000, 30000))
# Service ports that are always denied, even if they fall inside the allowed range.
DENIED_TUNNEL_PORTS = frozenset({22, 25, 80, 443, 587, 993, 3306, 5432, 6379, 8443})


def get_tunnel_base_domain() -> str:
    """Resolve the active tunnel base domain from Django settings."""
    return getattr(settings, 'TUNNEL_BASE_DOMAIN', 'tunnel.localhost')


# ── Serializers ──────────────────────────────────────────────────────────────

class TunnelRequestSerializer(serializers.ModelSerializer):
    responseTimeMs = serializers.IntegerField(
        source='response_time_ms', read_only=True)

    class Meta:
        model = TunnelRequest
        fields = [
            'id', 'method', 'path', 'status',
            'responseTimeMs', 'timestamp']


class TunnelSerializer(serializers.ModelSerializer):
    """Read serializer — camelCase fields for frontend compatibility."""
    tunnel_id = serializers.UUIDField(source='id', read_only=True)
    public_url = serializers.URLField(read_only=True)
    local_port = serializers.IntegerField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    request_count = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    bandwidth_used = serializers.IntegerField(
        source='bandwidth_bytes', read_only=True)
    shared_with = serializers.JSONField(read_only=True)
    expires_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Tunnel
        fields = [
            'tunnel_id', 'subdomain', 'public_url', 'local_port',
            'type', 'created_at', 'request_count', 'is_active',
            'bandwidth_used', 'shared_with', 'expires_at',
        ]


class TunnelCreateSerializer(serializers.Serializer):
    """Write serializer for creating tunnels from the dashboard UI."""
    local_port = serializers.IntegerField(min_value=1, max_value=65535)
    subdomain = serializers.CharField(
        max_length=63, required=False, allow_blank=True)
    type = serializers.ChoiceField(
        choices=['http', 'tcp'], default='http', required=False)

    def validate_local_port(self, value):
        if value in DENIED_TUNNEL_PORTS:
            raise serializers.ValidationError(
                f'Port {value} is a reserved service port and cannot be tunneled.'
            )
        if value not in ALLOWED_TUNNEL_PORTS:
            raise serializers.ValidationError(
                f'Port {value} is outside the allowed tunnel port ranges (1024-9999, 20000-29999).'
            )
        return value

    def validate_subdomain(self, value):
        if not value:
            return ''
        value = value.strip().lower()
        if not value.isalnum() and not all(
            c.isalnum() or c == '-' for c in value
        ):
            raise serializers.ValidationError(
                'Subdomain must contain only letters, numbers, and hyphens.')
        if value.startswith('-') or value.endswith('-'):
            raise serializers.ValidationError(
                'Subdomain cannot start or end with a hyphen.')
        if value in RESERVED_LABELS:
            raise serializers.ValidationError(
                f"Subdomain '{value}' is reserved and cannot be used.")
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        reserved_qs = ReservedSubdomain.objects.filter(
            subdomain=value, is_active=True,
        )
        if user and getattr(user, 'is_authenticated', False):
            reserved_qs = reserved_qs.exclude(owner=user)
        if reserved_qs.exists():
            raise serializers.ValidationError(
                'This subdomain is reserved by another user.')
        # Check uniqueness
        if Tunnel.objects.filter(
            subdomain=value, is_active=True
        ).exists():
            raise serializers.ValidationError(
                'This subdomain is already in use.')
        return value


# ── ViewSet ──────────────────────────────────────────────────────────────────

class TunnelViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing development tunnels.

    SECURITY: Zero Trust — users can only see their own tunnels.
    """
    queryset = Tunnel.objects.all()
    serializer_class = TunnelSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def get_queryset(self):
        return self.queryset.filter(
            owner=self.request.user, is_active=True)

    # ── List ─────────────────────────────────────────────────────────────

    def list(self, request):
        """GET /api/v1/tunnels/ — returns {tunnels: [...]}"""
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response({'tunnels': serializer.data})

    # ── Create (from UI) ─────────────────────────────────────────────────

    def create(self, request):
        """POST /api/v1/tunnels/ — create tunnel from dashboard."""
        write_serializer = TunnelCreateSerializer(
            data=request.data, context={'request': request},
        )
        write_serializer.is_valid(raise_exception=True)

        local_port = write_serializer.validated_data['local_port']
        subdomain = write_serializer.validated_data.get('subdomain', '')
        tunnel_type = write_serializer.validated_data.get('type', 'http')

        if not subdomain:
            subdomain = f"{request.user.username}-{uuid.uuid4().hex[:6]}"

        # Free any legacy soft-deleted tunnel row holding this unique subdomain slot
        for stale in Tunnel.objects.filter(subdomain=subdomain, is_active=False):
            stale.subdomain = f"{stale.subdomain[:45]}--del-{uuid.uuid4().hex[:8]}"
            stale.save(update_fields=['subdomain'])

        try:
            tunnel = Tunnel.objects.create(
                owner=request.user,
                subdomain=subdomain,
                local_port=local_port,
                type=tunnel_type,
                public_url=f"https://{subdomain}.{get_tunnel_base_domain()}",
                is_active=True,
            )
        except IntegrityError:
            return Response(
                {'error': 'This subdomain is already in use.'},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            TunnelSerializer(tunnel).data,
            status=status.HTTP_201_CREATED)

    # ── Delete (soft-delete) ─────────────────────────────────────────────

    def destroy(self, request, pk=None):
        """DELETE /api/v1/tunnels/{id}/ — soft-delete (deactivate)."""
        tunnel = self.get_object()
        tunnel.is_active = False
        tunnel.subdomain = f"{tunnel.subdomain[:45]}--del-{uuid.uuid4().hex[:8]}"
        tunnel.save(update_fields=['is_active', 'subdomain'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Requests Inspector ───────────────────────────────────────────────

    @action(detail=True, methods=['get'], url_path='requests')
    def get_requests(self, request, pk=None):
        """GET /api/v1/tunnels/{id}/requests/ — returns {requests: [...]}"""
        tunnel = self.get_object()
        tunnel_requests = tunnel.requests.all()[:100]
        serializer = TunnelRequestSerializer(tunnel_requests, many=True)
        return Response({'requests': serializer.data})

    # ── Replay ───────────────────────────────────────────────────────────

    @action(
        detail=True,
        methods=['post'],
        url_path=r'replay/(?P<request_id>[0-9a-f-]{36})',
    )
    def replay(self, request, pk=None, request_id=None):
        """POST /api/v1/tunnels/{id}/replay/{req_id}/ — replay a request"""
        tunnel = self.get_object()
        try:
            tunnel_req = tunnel.requests.get(id=request_id)
        except TunnelRequest.DoesNotExist:
            return Response(
                {'error': 'Request not found'},
                status=status.HTTP_404_NOT_FOUND)

        replayed = TunnelRequest.objects.create(
            tunnel=tunnel,
            method=tunnel_req.method,
            path=tunnel_req.path,
            headers=tunnel_req.headers,
            body_preview=tunnel_req.body_preview,
        )
        logger.info(
            "Replayed request %s as %s for tunnel %s",
            request_id, replayed.id, tunnel.subdomain)
        return Response(
            {'status': 'replayed', 'new_request_id': str(replayed.id)},
            status=status.HTTP_201_CREATED)

    # ── Share ────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'], url_path='share')
    def share(self, request, pk=None):
        """POST /api/v1/tunnels/{id}/share/ — share tunnel with a user."""
        tunnel = self.get_object()
        email = request.data.get('email', '').strip().lower()

        if not email or '@' not in email:
            return Response(
                {'error': 'Valid email address required.'},
                status=status.HTTP_400_BAD_REQUEST)

        shared = list(tunnel.shared_with or [])
        if email in shared:
            return Response(
                {'error': 'Already shared with this user.'},
                status=status.HTTP_409_CONFLICT)

        shared.append(email)
        tunnel.shared_with = shared
        tunnel.save(update_fields=['shared_with'])

        logger.info(
            "Tunnel %s shared with %s by %s",
            tunnel.subdomain, email, request.user.username)
        return Response({
            'status': 'shared',
            'shared_with': shared,
        })

    # ── Register (from CLI) ──────────────────────────────────────────────

    @action(detail=False, methods=['post'])
    def register(self, request):
        """POST /api/v1/tunnels/register/ — register tunnel from CLI tool"""
        subdomain = str(request.data.get('subdomain') or '').strip().lower()
        raw_port = request.data.get('local_port')

        if not subdomain or raw_port is None or isinstance(raw_port, bool):
            return Response(
                {'error': 'subdomain and integer local_port required'},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            local_port = int(raw_port)
        except (TypeError, ValueError):
            return Response(
                {'error': 'local_port must be an integer.'},
                status=status.HTTP_400_BAD_REQUEST)

        if local_port in DENIED_TUNNEL_PORTS:
            return Response(
                {'error': f'Port {local_port} is a reserved service port and cannot be tunneled.'},
                status=status.HTTP_400_BAD_REQUEST)

        if local_port not in ALLOWED_TUNNEL_PORTS:
            return Response(
                {'error': f'Port {local_port} is outside the allowed tunnel ranges (1024-9999, 20000-29999).'},
                status=status.HTTP_400_BAD_REQUEST)

        if (
            not all(c.isalnum() or c == '-' for c in subdomain)
            or subdomain.startswith('-')
            or subdomain.endswith('-')
            or len(subdomain) > 63
        ):
            return Response(
                {'error': 'Subdomain must contain only letters, numbers, and hyphens.'},
                status=status.HTTP_400_BAD_REQUEST)

        if subdomain in RESERVED_LABELS:
            return Response(
                {'error': f"Subdomain '{subdomain}' is reserved and cannot be used."},
                status=status.HTTP_400_BAD_REQUEST)

        if ReservedSubdomain.objects.filter(
            subdomain=subdomain, is_active=True,
        ).exclude(owner=request.user).exists():
            return Response(
                {'error': 'This subdomain is reserved by another user.'},
                status=status.HTTP_409_CONFLICT)

        if Tunnel.objects.filter(subdomain=subdomain, is_active=True).exclude(owner=request.user).exists():
            return Response(
                {'error': 'This subdomain is already in use.'},
                status=status.HTTP_409_CONFLICT)

        # Free any legacy soft-deleted tunnel row from another owner holding this slot
        for stale in Tunnel.objects.filter(subdomain=subdomain, is_active=False).exclude(owner=request.user):
            stale.subdomain = f"{stale.subdomain[:45]}--del-{uuid.uuid4().hex[:8]}"
            stale.save(update_fields=['subdomain'])

        try:
            tunnel, created = Tunnel.objects.update_or_create(
                subdomain=subdomain,
                owner=request.user,
                defaults={
                    'local_port': local_port,
                    'public_url': f"https://{subdomain}.{get_tunnel_base_domain()}",
                    'is_active': True,
                })
        except IntegrityError:
            return Response(
                {'error': 'This subdomain is already in use.'},
                status=status.HTTP_409_CONFLICT)

        return Response(
            TunnelSerializer(tunnel).data,
            status=status.HTTP_201_CREATED if created
            else status.HTTP_200_OK)
