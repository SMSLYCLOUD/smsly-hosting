"""
Views for reserved subdomain management.

Provides endpoints for the tunnels page subdomain reservation:
  - GET    /api/v1/subdomains/              → list user's reserved subdomains
  - POST   /api/v1/subdomains/              → reserve a subdomain
  - DELETE  /api/v1/subdomains/{subdomain}/ → release a subdomain
"""
from rest_framework import serializers, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models_tunnels import ReservedSubdomain, Tunnel
import logging
import re

logger = logging.getLogger(__name__)

# Max subdomains per user (adjustable per tier in future)
MAX_SUBDOMAINS_PER_USER = 5


class ReservedSubdomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReservedSubdomain
        fields = ['subdomain', 'created_at']


SUBDOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$')


@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def subdomains_list_create(request):
    """
    GET  /api/v1/subdomains/ — list reserved subdomains for the user.
    POST /api/v1/subdomains/ — reserve a new subdomain.
    """
    if request.method == 'GET':
        qs = ReservedSubdomain.objects.filter(owner=request.user)
        serializer = ReservedSubdomainSerializer(qs, many=True)
        return Response({
            'subdomains': serializer.data,
            'limit': MAX_SUBDOMAINS_PER_USER,
        })

    # POST — reserve
    subdomain = (request.data.get('subdomain') or '').strip().lower()
    if not subdomain:
        return Response(
            {'error': 'subdomain is required'},
            status=status.HTTP_400_BAD_REQUEST)

    if not SUBDOMAIN_RE.match(subdomain):
        return Response(
            {'error': 'Subdomain must be lowercase alphanumeric with hyphens, '
                      '1-63 chars, cannot start/end with hyphen.'},
            status=status.HTTP_400_BAD_REQUEST)

    # Check limit
    count = ReservedSubdomain.objects.filter(owner=request.user).count()
    if count >= MAX_SUBDOMAINS_PER_USER:
        return Response(
            {'error': f'Maximum {MAX_SUBDOMAINS_PER_USER} reserved subdomains allowed.'},
            status=status.HTTP_400_BAD_REQUEST)

    # Check conflicts with active tunnels or existing reservations
    if ReservedSubdomain.objects.filter(subdomain=subdomain).exists():
        return Response(
            {'error': 'This subdomain is already reserved.'},
            status=status.HTTP_409_CONFLICT)

    if Tunnel.objects.filter(subdomain=subdomain, is_active=True).exclude(
        owner=request.user
    ).exists():
        return Response(
            {'error': 'This subdomain is currently in use by another user.'},
            status=status.HTTP_409_CONFLICT)

    reservation = ReservedSubdomain.objects.create(
        owner=request.user,
        subdomain=subdomain,
    )

    logger.info(
        "Subdomain '%s' reserved by %s", subdomain, request.user.username)
    return Response(
        ReservedSubdomainSerializer(reservation).data,
        status=status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def subdomains_release(request, subdomain):
    """DELETE /api/v1/subdomains/{subdomain}/ — release a reserved subdomain."""
    try:
        reservation = ReservedSubdomain.objects.get(
            subdomain=subdomain, owner=request.user)
    except ReservedSubdomain.DoesNotExist:
        return Response(
            {'error': 'Subdomain reservation not found.'},
            status=status.HTTP_404_NOT_FOUND)

    reservation.delete()
    logger.info(
        "Subdomain '%s' released by %s", subdomain, request.user.username)
    return Response(status=status.HTTP_204_NO_CONTENT)
