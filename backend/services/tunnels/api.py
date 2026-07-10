# pylint:
"""Api module."""
# disable=line-too-long,too-few-public-methods,too-many-locals,no-member,too-many-return-statements,too-many-branches,unused-argument
"""
SMSLY Tunnel API Routes

Django REST API endpoints for tunnel management:
- List/create tunnels
- Reserve custom subdomains
- View request logs
- Replay requests
- Team sharing
"""

import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .rate_limit import RateLimiter

# Redis-backed storage (with in-memory fallback)
from .storage import tunnel_storage


def get_tunnel_base_domain() -> str:
    """Resolve the active tunnel base domain from Django settings."""
    return getattr(settings, 'TUNNEL_BASE_DOMAIN', 'tunnel.localhost')


class TunnelTier:
    """Tunnel service tiers."""
    FREE = 'free'
    PRO = 'pro'
    TEAM = 'team'

    LIMITS = {
        'free': {
            'tunnels': 1,
            'bandwidth': 100 * 1024 * 1024,  # 100MB/day
            'custom_subdomains': 0,
            'tcp': False,
            'timeout_hours': 2,
        },
        'pro': {
            'tunnels': 10,
            'bandwidth': 10 * 1024 * 1024 * 1024,  # 10GB/day
            'custom_subdomains': 5,
            'tcp': True,
            'timeout_hours': 24,
        },
        'team': {
            'tunnels': -1,  # Unlimited
            'bandwidth': -1,
            'custom_subdomains': -1,
            'tcp': True,
            'timeout_hours': 720,  # 30 days
        },
    }


def get_user_tier(user):
    """Get user's subscription tier."""
    if bool(getattr(settings, "SMSLY_DISABLE_TIER_GATES", False)):
        return TunnelTier.TEAM
    # Placeholder for billing integration
    if user.is_staff:
        return TunnelTier.TEAM
    return TunnelTier.FREE


def validate_subdomain(subdomain):
    """Validate subdomain format."""
    if not re.match(r'^[a-z0-9-]+$', subdomain):
        return False, "Subdomain can only contain lowercase letters, numbers, and dashes"
    if len(subdomain) < 3 or len(subdomain) > 63:
        return False, "Subdomain must be between 3 and 63 characters"
    if subdomain.startswith('-') or subdomain.endswith('-'):
        return False, "Subdomain cannot start or end with a dash"

    reserved = ['www', 'api', 'app', 'admin', 'tunnel', 'mail', 'ftp', 'ssh']
    if subdomain in reserved:
        return False, f"Subdomain '{subdomain}' is reserved"

    return True, ""


@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def tunnel_list(request):  # pylint: disable=too-many-return-statements
    # pylint: disable=too-many-return-statements
    """
    GET: List user's active tunnels
    POST: Create a new tunnel (called by CLI)
    """
    if request.method == 'GET':
        user_id = str(
            request.user.id) if request.user.is_authenticated else 'anonymous'
        user_tunnels = tunnel_storage.list_tunnels(user_id=user_id)

        return Response({
            'tunnels': user_tunnels,
            'count': len(user_tunnels),
        })

    # POST logic handles tunnel creation
    # pylint: disable=too-many-return-statements
    if request.method == 'POST':
        # Create new tunnel
        subdomain = request.data.get('subdomain')
        local_port = request.data.get('local_port', 3000)
        tunnel_type = request.data.get('type', 'http')  # http or tcp

        user_id = str(
            request.user.id) if request.user.is_authenticated else 'anonymous'
        tier = get_user_tier(request.user)
        limits = TunnelTier.LIMITS[tier]

        # Check tunnel limit
        user_tunnels = tunnel_storage.list_tunnels(user_id=user_id)
        if limits['tunnels'] != -1 and len(user_tunnels) >= limits['tunnels']:
            return Response(
                {
                    'error': f"Tunnel limit reached ({limits['tunnels']} for {tier} tier)"},
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
            reserved_info = tunnel_storage.get_subdomain(subdomain)
            if reserved_info:
                if reserved_info['user_id'] != user_id:
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
            if tunnel_storage.get_tunnel(subdomain):
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
            'public_url': f"https://{subdomain}.{get_tunnel_base_domain()}",
            'local_port': local_port,
            'type': tunnel_type,
            'user_id': user_id,
            'tier': tier,
            'created_at': timezone.now().isoformat(),
            'expires_at': expires_at,
            'request_count': 0,
            'is_active': True,
        }

        # Check rate limit
        _limiter = RateLimiter(requests_per_minute=60, requests_per_minute_anon=20)
        # Build a minimal request-like object for the limiter
        allowed, info = _limiter.is_allowed(request)
        if not allowed:
            return Response(
                {'error': 'Rate limit exceeded', 'retry_after': info.get('retry_after', 60)},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        tunnel_storage.set_tunnel(subdomain, tunnel)
        # No need to init empty log list for Redis, list created on push

        return Response(tunnel, status=status.HTTP_201_CREATED)
    return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


@api_view(['GET', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def tunnel_detail(request, tunnel_id):
    """Get or delete a specific tunnel."""
    tunnel = tunnel_storage.get_tunnel_by_id(tunnel_id)

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

    if request.method == 'DELETE':
        subdomain = tunnel['subdomain']
        tunnel_storage.delete_tunnel(subdomain)
        tunnel_storage.delete_request_logs(tunnel_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
    return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def tunnel_requests(request, tunnel_id):
    """Get request logs for a tunnel."""
    tunnel = tunnel_storage.get_tunnel_by_id(tunnel_id)
    if not tunnel:
        return Response({'error': 'Tunnel not found'},
                        status=status.HTTP_404_NOT_FOUND)
    user_id = str(request.user.id)
    if tunnel['user_id'] != user_id:
        return Response({'error': 'Not authorized'},
                        status=status.HTTP_403_FORBIDDEN)

    logs = tunnel_storage.get_request_logs(tunnel_id)
    return Response({
        'requests': logs,
        'total': len(logs),
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def replay_request(request, tunnel_id, request_id):
    """Replay a logged request."""
    # pylint: disable=unused-argument
    tunnel = tunnel_storage.get_tunnel_by_id(tunnel_id)
    if not tunnel:
        return Response({'error': 'Tunnel not found'},
                        status=status.HTTP_404_NOT_FOUND)
    user_id = str(request.user.id)
    if tunnel['user_id'] != user_id:
        return Response({'error': 'Not authorized'},
                        status=status.HTTP_403_FORBIDDEN)

    logs = tunnel_storage.get_request_logs(tunnel_id)
    log_entry = next((entry for entry in logs if entry['request_id'] == request_id), None)

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
        user_subdomains = tunnel_storage.list_subdomains(user_id=user_id)
        return Response({
            'subdomains': user_subdomains,
            'limit': limits['custom_subdomains'],
        })

    if request.method == 'POST':
        subdomain = request.data.get('subdomain', '').lower()

        # Validate
        valid, error = validate_subdomain(subdomain)
        if not valid:
            return Response({'error': error},
                            status=status.HTTP_400_BAD_REQUEST)

        # Check limit
        user_subdomains = tunnel_storage.list_subdomains(user_id=user_id)
        if limits['custom_subdomains'] != - \
                1 and len(user_subdomains) >= limits['custom_subdomains']:
            return Response(
                {
                    'error': f"Subdomain limit reached ({limits['custom_subdomains']} for {tier} tier)"},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check availability (reserved or active)
        if tunnel_storage.get_subdomain(subdomain):
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
        tunnel_storage.set_subdomain(subdomain, reservation)

        return Response(reservation, status=status.HTTP_201_CREATED)
    return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def subdomain_delete(request, subdomain):
    """Release a reserved subdomain."""
    user_id = str(request.user.id)

    reserved_info = tunnel_storage.get_subdomain(subdomain)
    if not reserved_info:
        return Response({'error': 'Subdomain not found'},
                        status=status.HTTP_404_NOT_FOUND)

    if reserved_info['user_id'] != user_id:
        return Response({'error': 'Not authorized'},
                        status=status.HTTP_403_FORBIDDEN)

    tunnel_storage.delete_subdomain(subdomain)
    return Response(status=status.HTTP_204_NO_CONTENT)


# ==================== TEAM SHARING ====================

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def share_tunnel(request, tunnel_id):
    """Share a tunnel with team members."""
    tunnel = tunnel_storage.get_tunnel_by_id(tunnel_id)

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
        # Update storage
        tunnel_storage.set_tunnel(tunnel['subdomain'], tunnel)

    return Response({
        'status': 'shared',
        'tunnel_id': tunnel_id,
        'shared_with': tunnel['shared_with'],
    })


# ==================== URL PATTERNS ====================

def get_urlpatterns():
    """Get URL patterns for tunnel API."""
    from django.urls import path  # pylint: disable=import-outside-toplevel

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
