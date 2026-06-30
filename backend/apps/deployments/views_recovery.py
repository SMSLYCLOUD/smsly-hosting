"""Recovery phrase views — generate, verify, and use recovery phrases."""
import json
import logging

from django.contrib.auth import get_user_model, login
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from .models_core import PlatformConfig
from .services.recovery import (
    generate_recovery_phrase,
    generate_recovery_salt,
    hash_recovery_phrase,
    verify_recovery_phrase,
)

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def recovery_phrase_generate(request):
    """
    Generate a new 12-word recovery phrase and store its hash.

    The phrase is displayed ONCE to the user and then only the salted
    SHA-256 hash is stored. The platform cannot recover the phrase.
    """
    config = PlatformConfig.load()

    phrase = generate_recovery_phrase()
    salt = generate_recovery_salt()
    phrase_hash = hash_recovery_phrase(phrase, salt)

    config.recovery_phrase_hash = json.dumps({'hash': phrase_hash, 'salt': salt})
    config.save(update_fields=['recovery_phrase_hash'])

    return Response({
        'phrase': ' '.join(phrase),
        'message': 'Write this down and store it securely. It will not be shown again.',
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@throttle_classes([AnonRateThrottle])
def recovery_phrase_verify(request):
    """
    Verify a recovery phrase and log the user in.

    This is the last-resort account recovery mechanism. If all trusted
    devices are lost, the 12-word phrase can restore admin access.
    """
    phrase_str = request.data.get('phrase', '').strip().lower()
    username = request.data.get('username', '').strip()

    if not phrase_str or not username:
        return Response({'error': 'Phrase and username are required'},
                        status=status.HTTP_400_BAD_REQUEST)

    words = phrase_str.split()
    if len(words) != 12:
        return Response({'error': 'Recovery phrase must be exactly 12 words'},
                        status=status.HTTP_400_BAD_REQUEST)

    config = PlatformConfig.load()
    stored = config.recovery_phrase_hash or ''

    if not stored:
        return Response({'error': 'No recovery phrase has been set up'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        stored_data = json.loads(stored)
        stored_hash = stored_data.get('hash', '')
        salt = stored_data.get('salt', '')
    except (json.JSONDecodeError, TypeError):
        return Response({'error': 'Recovery configuration is corrupted'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    if not verify_recovery_phrase(words, stored_hash, salt):
        client_ip = (
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', 'unknown')
        )
        logger.warning("Failed recovery phrase attempt from %s for user '%s'", client_ip, username)
        return Response({'error': 'Invalid recovery phrase'},
                        status=status.HTTP_403_FORBIDDEN)

    User = get_user_model()
    user = User.objects.filter(username=username, is_superuser=True).first()
    if not user:
        return Response({'error': 'Admin user not found'},
                        status=status.HTTP_404_NOT_FOUND)

    login(request, user)
    return Response({
        'success': True,
        'message': 'Recovery successful. Set up a new trusted device immediately.',
    })
