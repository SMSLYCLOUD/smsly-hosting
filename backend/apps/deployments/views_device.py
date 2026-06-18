"""Device trust API — hardware-fingerprint-based device enrollment."""
import hashlib
import hmac
import json
import logging
import secrets
import time

from django.conf import settings
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models_core import TrustedDevice

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def register_device(request):
    """
    Register the current device as a trusted device.

    Accepts a fingerprint object from the frontend (canvas hash, platform,
    CPU cores, screen resolution, etc.) and generates a unique device token
    that the browser stores in localStorage for subsequent requests.

    POST body:
      {
        "fingerprint": {
          "canvas": "sha256hash...",
          "platform": "Win32",
          "cpu_cores": 8,
          "screen": "1920x1080",
          "timezone": -60,
          "languages": ["en-US"],
          "fonts": ["Arial", "Helvetica", ...]
        },
        "label": "Work Laptop"  // optional
      }
    """
    fingerprint = request.data.get('fingerprint')
    label = str(request.data.get('label', '') or '').strip()

    if not fingerprint or not isinstance(fingerprint, dict):
        return Response({'error': 'Device fingerprint is required'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Compute a stable hash from the fingerprint components
    raw = json.dumps(fingerprint, sort_keys=True, separators=(',', ':'))
    fingerprint_hash = hashlib.sha256(raw.encode()).hexdigest()

    # Check if this exact fingerprint was already enrolled
    existing = TrustedDevice.objects.filter(
        user=request.user,
        fingerprint_hash=fingerprint_hash,
        is_active=True,
    ).first()
    if existing:
        existing.last_seen_at = timezone.now()
        existing.label = label or existing.label
        existing.ip_address = request.META.get('REMOTE_ADDR', '')
        existing.user_agent = request.META.get('HTTP_USER_AGENT', '')[:1000]
        existing.save(update_fields=['last_seen_at', 'label', 'ip_address', 'user_agent'])
        return Response({
            'device_token': existing.device_token,
            'is_new': False,
            'message': 'Device already trusted',
        })

    # Generate a cryptographically random device token
    device_token = secrets.token_urlsafe(48)

    device = TrustedDevice.objects.create(
        user=request.user,
        device_token=device_token,
        fingerprint_hash=fingerprint_hash,
        label=label,
        ip_address=request.META.get('REMOTE_ADDR', ''),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000],
    )

    return Response({
        'device_token': device_token,
        'is_new': True,
        'message': 'Device registered successfully',
    }, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def list_devices(request):
    """List all trusted devices for the current user."""
    devices = TrustedDevice.objects.filter(user=request.user, is_active=True)
    return Response({
        'devices': [
            {
                'id': str(d.id),
                'label': d.label,
                'fingerprint_hash': d.fingerprint_hash[:16] + '...',
                'ip_address': d.ip_address,
                'last_seen_at': d.last_seen_at.isoformat() if d.last_seen_at else None,
                'created_at': d.created_at.isoformat(),
            }
            for d in devices
        ]
    })


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def revoke_device(request, device_id):
    """Revoke a trusted device by its ID."""
    try:
        device = TrustedDevice.objects.get(id=device_id, user=request.user)
        device.is_active = False
        device.save(update_fields=['is_active'])
        return Response({'message': 'Device revoked'})
    except TrustedDevice.DoesNotExist:
        return Response({'error': 'Device not found'},
                        status=status.HTTP_404_NOT_FOUND)
