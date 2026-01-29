"""
SMSLY Tunnel API Routes

Django REST API endpoints for tunnel management:
- List/create tunnels
- Reserve custom subdomains
- View request logs
- Replay requests
- Team sharing
"""

from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from datetime import timedelta
import uuid
import re


# In-memory storage (replace with Redis/DB in production)
_tunnels = {}
_reserved_subdomains = {}
_request_logs = {}


class TunnelTier:
    FREE = 'free'
    PRO = 'pro'
    TEAM = 'team'

    LIMITS = {
        FREE: {'tunnels': 1, 'custom_subdomains': 0, 'tcp': False, 'timeout_hours': 8},
        PRO: {'tunnels': 5, 'custom_subdomains': 3, 'tcp': False, 'timeout_hours': None},
        TEAM: {'tunnels': -1, 'custom_subdomains': -1, 'tcp': True, 'timeout_hours': None},
    }


def get_user_tier(user):
    """Get user's tunnel tier from subscription."""
    # Integration with billing system
    # For now, default to PRO for authenticated users
    if user.is_authenticated:
        return TunnelTier.PRO
    return TunnelTier.FREE


def validate_subdomain(subdomain: str) -> tuple[bool, str]:
    """Validate subdomain format."""
    if not subdomain:
        return False, "Subdomain is required"
    if len(subdomain) < 3:
        return False, "Subdomain must be at least 3 characters"
    if len(subdomain) > 32:
        return False, "Subdomain must be at most 32 characters"
    if not re.match(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$', subdomain):
        return False, "Subdomain must be lowercase alphanumeric with hyphens"

    # Reserved subdomains
    reserved = ['www', 'api', 'app', 'admin', 'tunnel', 'mail', 'ftp', 'ssh']
    if subdomain in reserved:
        return False, f"Subdomain '{subdomain}' is reserved"

    return True, ""


@api_view(['GET', 'POST'])
@permission_classes([permissions.AllowAny])
def tunnel_list(request):
    """
    GET: List user's active tunnels
    POST: Create a new tunnel (called by CLI)
    """
    if request.method == 'GET':
        user_id = str(
            request.user.id) if request.user.is_authenticated else 'anonymous'
        user_tunnels = [
            t for t in _tunnels.values() if t.get('user_id') == user_id]

        return Response({
            'tunnels': user_tunnels,
            'count': len(user_tunnels),
        })

    elif request.method == 'POST':
        # Create new tunnel
        subdomain = request.data.get('subdomain')
        local_port = request.data.get('local_port', 3000)
        tunnel_type = request.data.get('type', 'http')  # http or tcp

        user_id = str(
            request.user.id) if request.user.is_authenticated else 'anonymous'
        tier = get_user_tier(request.user)
        limits = TunnelTier.LIMITS[tier]

        # Check tunnel limit
        user_tunnels = [
            t for t in _tunnels.values() if t.get('user_id') == user_id]
        if limits['tunnels'] != -1 and len(user_tunnels) >= limits['tunnels']:
            return Response(
                {
                    'error': f"Tunnel limit reached ({
                        limits['tunnels']} for {tier} tier)"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check TCP permission
        if tunnel_type == 'tcp' and not limits['tcp']:
            return Response(
                {'error': "TCP tunnels require Team tier"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Validate/generate subdomain
        if subdomain:
            valid, error = validate_subdomain(subdomain)
            if not valid:
                return Response({'error': error},
                                status=status.HTTP_400_BAD_REQUEST)

            # Check if reserved by user
            if subdomain in _reserved_subdomains:
                if _reserved_subdomains[subdomain]['user_id'] != user_id:
                    return Response(
                        {'error': f"Subdomain '{subdomain}' is reserved by another user"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            elif limits['custom_subdomains'] == 0:
                return Response(
                    {'error': "Custom subdomains require Pro tier"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # Check if in use
            if subdomain in _tunnels:
                return Response(
                    {'error': f"Subdomain '{subdomain}' is currently in use"},
                    status=status.HTTP_409_CONFLICT
                )
        else:
            subdomain = uuid.uuid4().hex[:8]

        # Calculate expiry
        expires_at = None
        if limits['timeout_hours']:
            expires_at = (
                timezone.now() +
                timedelta(
                    hours=limits['timeout_hours'])).isoformat()

        # Create tunnel
        tunnel_id = str(uuid.uuid4())
        tunnel = {
            'tunnel_id': tunnel_id,
            'subdomain': subdomain,
            'public_url': f"https://{subdomain}.tunnel.smsly.cloud",
            'local_port': local_port,
            'type': tunnel_type,
            'user_id': user_id,
            'tier': tier,
            'created_at': timezone.now().isoformat(),
            'expires_at': expires_at,
            'request_count': 0,
            'is_active': True,
        }

        _tunnels[subdomain] = tunnel
        _request_logs[tunnel_id] = []

        return Response(tunnel, status=status.HTTP_201_CREATED)


@api_view(['GET', 'DELETE'])
@permission_classes([permissions.AllowAny])
def tunnel_detail(request, tunnel_id):
    """Get or delete a specific tunnel."""
    tunnel = next((t for t in _tunnels.values()
                  if t['tunnel_id'] == tunnel_id), None)

    if not tunnel:
        return Response({'error': 'Tunnel not found'},
                        status=status.HTTP_404_NOT_FOUND)

    user_id = str(
        request.user.id) if request.user.is_authenticated else 'anonymous'
    if tunnel['user_id'] != user_id:
        return Response({'error': 'Not authorized'},
                        status=status.HTTP_403_FORBIDDEN)

    if request.method == 'GET':
        return Response(tunnel)

    elif request.method == 'DELETE':
        subdomain = tunnel['subdomain']
        if subdomain in _tunnels:
            del _tunnels[subdomain]
        if tunnel_id in _request_logs:
            del _request_logs[tunnel_id]
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def tunnel_requests(request, tunnel_id):
    """Get request logs for a tunnel."""
    logs = _request_logs.get(tunnel_id, [])
    return Response({
        'requests': logs[-100:],  # Last 100
        'total': len(logs),
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def replay_request(request, tunnel_id, request_id):
    """Replay a logged request."""
    logs = _request_logs.get(tunnel_id, [])
    log_entry = next((l for l in logs if l['request_id'] == request_id), None)

    if not log_entry:
        return Response({'error': 'Request not found'},
                        status=status.HTTP_404_NOT_FOUND)

    # Mark as replayed (actual replay happens via WebSocket)
    return Response({
        'status': 'queued',
        'request_id': request_id,
        'original_request': log_entry,
    })


# ==================== SUBDOMAIN RESERVATION ====================

@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def subdomain_list(request):
    """
    GET: List user's reserved subdomains
    POST: Reserve a new subdomain
    """
    user_id = str(request.user.id)
    tier = get_user_tier(request.user)
    limits = TunnelTier.LIMITS[tier]

    if request.method == 'GET':
        user_subdomains = [
            s for s in _reserved_subdomains.values() if s['user_id'] == user_id]
        return Response({
            'subdomains': user_subdomains,
            'limit': limits['custom_subdomains'],
        })

    elif request.method == 'POST':
        subdomain = request.data.get('subdomain', '').lower()

        # Validate
        valid, error = validate_subdomain(subdomain)
        if not valid:
            return Response({'error': error},
                            status=status.HTTP_400_BAD_REQUEST)

        # Check limit
        user_subdomains = [
            s for s in _reserved_subdomains.values() if s['user_id'] == user_id]
        if limits['custom_subdomains'] != - \
                1 and len(user_subdomains) >= limits['custom_subdomains']:
            return Response(
                {
                    'error': f"Subdomain limit reached ({
                        limits['custom_subdomains']} for {tier} tier)"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check availability
        if subdomain in _reserved_subdomains:
            return Response(
                {'error': f"Subdomain '{subdomain}' is already reserved"},
                status=status.HTTP_409_CONFLICT
            )

        # Reserve
        reservation = {
            'subdomain': subdomain,
            'user_id': user_id,
            'created_at': timezone.now().isoformat(),
        }
        _reserved_subdomains[subdomain] = reservation

        return Response(reservation, status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def subdomain_delete(request, subdomain):
    """Release a reserved subdomain."""
    user_id = str(request.user.id)

    if subdomain not in _reserved_subdomains:
        return Response({'error': 'Subdomain not found'},
                        status=status.HTTP_404_NOT_FOUND)

    if _reserved_subdomains[subdomain]['user_id'] != user_id:
        return Response({'error': 'Not authorized'},
                        status=status.HTTP_403_FORBIDDEN)

    del _reserved_subdomains[subdomain]
    return Response(status=status.HTTP_204_NO_CONTENT)


# ==================== TEAM SHARING ====================

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def share_tunnel(request, tunnel_id):
    """Share a tunnel with team members."""
    tunnel = next((t for t in _tunnels.values()
                  if t['tunnel_id'] == tunnel_id), None)

    if not tunnel:
        return Response({'error': 'Tunnel not found'},
                        status=status.HTTP_404_NOT_FOUND)

    user_id = str(request.user.id)
    if tunnel['user_id'] != user_id:
        return Response({'error': 'Not authorized'},
                        status=status.HTTP_403_FORBIDDEN)

    # Check tier
    tier = get_user_tier(request.user)
    if tier != TunnelTier.TEAM:
        return Response(
            {'error': 'Team sharing requires Team tier'},
            status=status.HTTP_403_FORBIDDEN
        )

    email = request.data.get('email')
    if not email:
        return Response({'error': 'Email is required'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Add to shared list
    if 'shared_with' not in tunnel:
        tunnel['shared_with'] = []

    if email not in tunnel['shared_with']:
        tunnel['shared_with'].append(email)

    return Response({
        'status': 'shared',
        'tunnel_id': tunnel_id,
        'shared_with': tunnel['shared_with'],
    })


# ==================== URL PATTERNS ====================

def get_urlpatterns():
    """Get URL patterns for tunnel API."""
    from django.urls import path

    return [
        path('tunnels/', tunnel_list, name='tunnel-list'),
        path('tunnels/<str:tunnel_id>/', tunnel_detail, name='tunnel-detail'),
        path(
            'tunnels/<str:tunnel_id>/requests/',
            tunnel_requests,
            name='tunnel-requests'),
        path('tunnels/<str:tunnel_id>/replay/<str:request_id>/',
             replay_request, name='replay-request'),
        path(
            'tunnels/<str:tunnel_id>/share/',
            share_tunnel,
            name='share-tunnel'),
        path('subdomains/', subdomain_list, name='subdomain-list'),
        path(
            'subdomains/<str:subdomain>/',
            subdomain_delete,
            name='subdomain-delete'),
    ]
