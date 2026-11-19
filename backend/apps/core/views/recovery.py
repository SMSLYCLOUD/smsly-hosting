"""Recovery phrase views — generate, verify, and revoke recovery phrases."""
from datetime import datetime, timedelta, timezone as dt_timezone
import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from apps.core.models.api_token import CanManageAPITokens
from apps.core.models.audit import AuditLog
from apps.core.rate_limiting import TrustedIdentMixin
from apps.deployments.models.core import PlatformConfig
from apps.core.services.recovery import (
    generate_recovery_phrase,
    generate_recovery_salt,
    hash_recovery_phrase,
    verify_recovery_phrase,
)

logger = logging.getLogger(__name__)

_DEFAULT_RECOVERY_TTL_DAYS = 90


def _recovery_ttl_days() -> int:
    try:
        days = int(getattr(settings, "RECOVERY_PHRASE_TTL_DAYS", _DEFAULT_RECOVERY_TTL_DAYS))
    except (TypeError, ValueError):
        days = _DEFAULT_RECOVERY_TTL_DAYS
    return max(1, days)


def _record_recovery_audit(actor: str, action: str, target: str, metadata: dict) -> None:
    try:
        AuditLog(actor=actor[:255], action=action, target=target[:255], metadata=metadata).save()
    except Exception as exc:
        logger.warning("Recovery audit log failed (%s): %s", action, exc)


class RecoveryPhraseThrottle(TrustedIdentMixin, AnonRateThrottle):
    """Stricter rate limit for recovery phrase attempts.

    Uses TrustedIdentMixin (not the default DRF ident): behind
    Cloudflare the stock ident hashes the rotating XFF edge IP, so
    every request lands in a fresh bucket and the guard never fires.
    """
    scope = 'recovery_phrase'
    rate = '5/hour'  # 5 attempts per hour per IP


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, CanManageAPITokens])
def recovery_phrase_generate(request):
    """
    Generate a new single-use 12-word recovery phrase with rotation metadata.

    Only POST with an active superuser session/unrestricted token is accepted.
    The phrase is displayed ONCE to the user and then only the salted
    SHA-256 hash + expiration timestamp is stored.
    """
    if not (request.user.is_superuser and request.user.is_active):
        return Response({"detail": "Only an active superuser can generate the recovery phrase."},
                        status=status.HTTP_403_FORBIDDEN)

    config = PlatformConfig.load()

    phrase = generate_recovery_phrase()
    salt = generate_recovery_salt()
    phrase_hash = hash_recovery_phrase(phrase, salt)
    now = datetime.now(dt_timezone.utc)
    ttl_days = _recovery_ttl_days()
    expires_at = (now + timedelta(days=ttl_days)).isoformat()
    created_at = now.isoformat()

    config.recovery_phrase_hash = json.dumps({
        'hash': phrase_hash,
        'salt': salt,
        'created_at': created_at,
        'expires_at': expires_at,
        'single_use': True,
    })
    config.save(update_fields=['recovery_phrase_hash'])

    _record_recovery_audit(
        request.user.get_username() or "superuser",
        "RECOVERY_PHRASE_GENERATED",
        "PlatformConfig:1",
        {"created_at": created_at, "expires_at": expires_at, "single_use": True},
    )

    return Response({
        'phrase': ' '.join(phrase),
        'created_at': created_at,
        'expires_at': expires_at,
        'single_use': True,
        'message': 'Write this down and store it securely. Single-use; it will not be shown again.',
    })


@api_view(['POST', 'DELETE'])
@permission_classes([permissions.IsAuthenticated, CanManageAPITokens])
def recovery_phrase_revoke(request):
    """Explicitly invalidate the stored platform recovery phrase."""
    if not (request.user.is_superuser and request.user.is_active):
        return Response(
            {"detail": "Only an active superuser can revoke the recovery phrase."},
            status=status.HTTP_403_FORBIDDEN,
        )
    config = PlatformConfig.load()
    had_phrase = bool((config.recovery_phrase_hash or "").strip())
    if had_phrase:
        config.recovery_phrase_hash = ""
        config.save(update_fields=['recovery_phrase_hash'])
    _record_recovery_audit(
        request.user.get_username() or "superuser",
        "RECOVERY_PHRASE_REVOKED",
        "PlatformConfig:1",
        {"had_phrase": had_phrase},
    )
    return Response({'revoked': True, 'had_phrase': had_phrase})


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@throttle_classes([RecoveryPhraseThrottle])
def recovery_phrase_verify(request):
    """
    Verify a recovery phrase and log the user in (single-use consumption).

    This is the last-resort account recovery mechanism. Once redeemed cleanly
    (or if expired), the stored recovery phrase hash is invalidated immediately
    so the phrase cannot be replayed indefinitely.
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

    client_ip = RecoveryPhraseThrottle().get_ident(request)

    # Track failed attempts atomically in cache for lockout
    from django.core.cache import cache
    lockout_key = f"recovery_attempts:{client_ip}"
    if cache.add(lockout_key, 1, timeout=3600):
        attempts = 1
    else:
        try:
            attempts = cache.incr(lockout_key)
        except ValueError:
            cache.set(lockout_key, 1, timeout=3600)
            attempts = 1
    if attempts > 10:
        return Response(
            {'error': 'Too many failed attempts. Try again later.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    User = get_user_model()
    with transaction.atomic():
        config = PlatformConfig.load()
        config = (
            PlatformConfig.objects.select_for_update().filter(pk=config.pk).first()
            or config
        )
        stored = config.recovery_phrase_hash or ''

        if not stored:
            return Response({'error': 'No recovery phrase has been set up'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            stored_data = json.loads(stored)
            stored_hash = stored_data.get('hash', '')
            salt = stored_data.get('salt', '')
            expires_at_raw = stored_data.get('expires_at')
        except (json.JSONDecodeError, TypeError):
            return Response({'error': 'Recovery configuration is corrupted'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if expires_at_raw:
            try:
                expires_dt = datetime.fromisoformat(str(expires_at_raw))
                if expires_dt.tzinfo is None:
                    expires_dt = expires_dt.replace(tzinfo=dt_timezone.utc)
                if datetime.now(dt_timezone.utc) >= expires_dt:
                    config.recovery_phrase_hash = ''
                    config.save(update_fields=['recovery_phrase_hash'])
                    _record_recovery_audit(
                        username or "anonymous",
                        "RECOVERY_PHRASE_EXPIRED",
                        "PlatformConfig:1",
                        {"ip": client_ip},
                    )
                    return Response(
                        {'error': 'Recovery phrase has expired. Generate a new recovery phrase.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except ValueError:
                pass

        if not verify_recovery_phrase(words, stored_hash, salt):
            logger.warning("Failed recovery phrase attempt from %s for user '%s'", client_ip, username)
            return Response({'error': 'Invalid recovery phrase'},
                            status=status.HTTP_403_FORBIDDEN)

        user = User.objects.filter(username=username, is_superuser=True, is_active=True).first()
        if not user:
            return Response({'error': 'Admin user not found'},
                            status=status.HTTP_404_NOT_FOUND)

        # Consume single-use recovery phrase immediately under the row lock.
        config.recovery_phrase_hash = ''
        config.save(update_fields=['recovery_phrase_hash'])

    # Successful verification — clear lockout counter and log audit event
    cache.delete(lockout_key)
    _record_recovery_audit(
        user.get_username() or username,
        "RECOVERY_PHRASE_CONSUMED",
        f"User:{user.id}",
        {"ip": client_ip},
    )

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    from rest_framework.authtoken.models import Token
    from apps.core.auth_cookies import set_auth_cookie
    token, _ = Token.objects.get_or_create(user=user)
    response = Response({
        'success': True,
        'phrase_consumed': True,
        'message': 'Recovery successful. Single-use phrase consumed; set up a new trusted device and rotate recovery phrase.',
        'key': token.key,
    })
    try:
        set_auth_cookie(response, token.key)
    except Exception:
        pass
    return response
