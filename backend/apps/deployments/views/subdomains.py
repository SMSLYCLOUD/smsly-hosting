"""
Views for reserved subdomain management.

Provides endpoints for the tunnels page subdomain reservation:
  - GET    /api/v1/subdomains/              → list user's reserved subdomains
  - POST   /api/v1/subdomains/              → reserve a subdomain
  - DELETE  /api/v1/subdomains/{subdomain}/ → release a subdomain
"""
import logging
import re
from datetime import timedelta

from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from ..models.tunnels import ReservedSubdomain, Tunnel

logger = logging.getLogger(__name__)

# Reserved labels that cannot be used as subdomains
# Only block subdomains that would conflict with the platform's own HTTP routing
# or that are reserved by IANA/RFC for special use.
RESERVED_LABELS = frozenset({'admin', 'api', 'mail', 'smtp', 'imap'})

# Max subdomains per user (adjustable per tier in future)
MAX_SUBDOMAINS_PER_USER = 5

# After releasing a subdomain, the same owner cannot re-claim it for this
# many hours. This prevents the delete+re-add bypass of per-user quota
# enforcement (otherwise a user could release a subdomain and immediately
# reserve a different one, ignoring the cap).
SUBDOMAIN_RELEASE_COOLDOWN_HOURS = 24


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
        qs = ReservedSubdomain.objects.filter(
            owner=request.user, is_active=True,
        )
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
    count = ReservedSubdomain.objects.filter(
        owner=request.user, is_active=True,
    ).count()
    if count >= MAX_SUBDOMAINS_PER_USER:
        return Response(
            {'error': f'Maximum {MAX_SUBDOMAINS_PER_USER} reserved subdomains allowed.'},
            status=status.HTTP_400_BAD_REQUEST)

    # Check conflicts with active tunnels or existing reservations
    if ReservedSubdomain.objects.filter(
        subdomain=subdomain, is_active=True,
    ).exists():
        return Response(
            {'error': 'This subdomain is already reserved.'},
            status=status.HTTP_409_CONFLICT)

    if Tunnel.objects.filter(subdomain=subdomain, is_active=True).exclude(
        owner=request.user
    ).exists():
        return Response(
            {'error': 'This subdomain is currently in use by another user.'},
            status=status.HTTP_409_CONFLICT)

    # Enforce a cooldown after release: the same user cannot re-claim a
    # subdomain they released within the last SUBDOMAIN_RELEASE_COOLDOWN_HOURS.
    # This blocks the delete+re-add bypass used to evade per-user quotas.
    last_release = (
        ReservedSubdomain.objects
        .filter(
            subdomain=subdomain,
            owner=request.user,
            is_active=False,
            released_at__isnull=False,
        )
        .order_by('-released_at')
        .first()
    )
    if last_release is not None:
        cooldown_end = last_release.released_at + timedelta(
            hours=SUBDOMAIN_RELEASE_COOLDOWN_HOURS
        )
        if timezone.now() < cooldown_end:
            return Response(
                {
                    'error': (
                        f'Subdomain was released in the last '
                        f'{SUBDOMAIN_RELEASE_COOLDOWN_HOURS}h; cooldown ends '
                        f'at {cooldown_end.isoformat()}.'
                    )
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

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
            subdomain=subdomain, owner=request.user, is_active=True)
    except ReservedSubdomain.DoesNotExist:
        return Response(
            {'error': 'Subdomain reservation not found.'},
            status=status.HTTP_404_NOT_FOUND)

    # Soft-release: keep the row so the cooldown check can still observe the
    # release timestamp. The DB unique constraint is partial on is_active=True
    # so the name is freed for a fresh reservation once the cooldown elapses.
    reservation.released_at = timezone.now()
    reservation.is_active = False
    reservation.save(update_fields=['released_at', 'is_active'])
    logger.info(
        "Subdomain '%s' released by %s", subdomain, request.user.username)
    return Response(status=status.HTTP_204_NO_CONTENT)
