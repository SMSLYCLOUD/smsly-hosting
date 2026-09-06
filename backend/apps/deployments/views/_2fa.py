"""
Two-Factor Authentication (TOTP) views.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django_otp import devices_for_user, user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from apps.core.rate_limiting import TwoFactorLoginRateThrottle

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def two_factor_status(request):
    """Check if the current user has 2FA enabled."""
    has_2fa = user_has_device(request.user, confirmed=True)
    return Response({'enabled': has_2fa})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def two_factor_enable(request):
    """
    Enable TOTP 2FA for the current user.

    Generates a new TOTP device and returns the provisioning URI
    (for QR code generation on the frontend).
    """
    # Remove any unconfirmed devices first
    for device in devices_for_user(request.user, confirmed=False):
        device.delete()

    device = TOTPDevice.objects.create(
        user=request.user,
        name='default',
        confirmed=False,
        tolerance=1,  # Allow 1 step time drift
        ttl=30,       # 30-second tokens
    )

    # Build provisioning URI
    issuer = getattr(settings, 'TOTP_ISSUER', 'Grid PaaS') or 'Grid PaaS'
    uri = device.config_url(issuer=issuer)

    return Response({
        'provisioning_uri': uri,
        'device_id': str(device.id),
        'secret': device.key,
        'warning': (
            '⚠️ 2FA is OPTIONAL. Before enabling, ensure you have backup codes '
            'or a recovery phrase saved. If you lose your authenticator device '
            'and have no backup method, you will be locked out of your account. '
            'Generate backup codes at GET /api/v1/auth/2fa/backup-codes/ AFTER '
            'confirming setup. Store them somewhere safe.'
        ),
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def two_factor_confirm(request):
    """
    Confirm TOTP device by verifying a valid token.
    This completes the 2FA enrollment.
    """
    token = request.data.get('token', '')
    device_id = request.data.get('device_id', '')

    if not token or not device_id:
        return Response({'error': 'Token and device_id are required'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        device = TOTPDevice.objects.get(id=device_id, user=request.user, confirmed=False)
    except TOTPDevice.DoesNotExist:
        return Response({'error': 'Device not found or already confirmed'},
                        status=status.HTTP_404_NOT_FOUND)

    if device.verify_token(token):
        device.confirmed = True
        device.save()
        return Response({'enabled': True, 'message': '2FA enabled successfully'})

    return Response({'error': 'Invalid token. Try again with a new code from your authenticator app.'},
                    status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def two_factor_disable(request):
    """
    Disable 2FA by verifying the current password.
    """
    password = request.data.get('password', '')
    if not password:
        return Response({'error': 'Current password is required to disable 2FA'},
                        status=status.HTTP_400_BAD_REQUEST)

    user = request.user
    if not user.check_password(password):
        return Response({'error': 'Invalid password'},
                        status=status.HTTP_403_FORBIDDEN)

    # Remove all TOTP devices
    for device in devices_for_user(user, confirmed=True):
        device.delete()

    return Response({'enabled': False, 'message': '2FA disabled'})


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@throttle_classes([TwoFactorLoginRateThrottle])
def two_factor_login(request):
    """
    Verify a 2FA token during login (step 2 of the 2FA handshake).

    Expects the session has `2fa_user_id` set (from the first login
    step — password login or OAuth session-token exchange). On success
    this is the ONLY path that mints the DRF token for 2FA-enrolled
    users: it session-logins (rotating the session key), clears the
    pending handshake, creates/gets the DRF token, stamps the HttpOnly
    auth cookie, and returns the key in the body for API clients.

    Defense in depth: wrong codes are counted per session
    (MAX_2FA_ATTEMPTS_PER_SESSION); exceeding the cap destroys the
    pending handshake and forces the attacker back to the password
    step — on top of the 10/min/IP TwoFactorLoginRateThrottle.
    """
    from django.contrib.auth import get_user_model, login

    from apps.core.auth_2fa import (
        MAX_2FA_ATTEMPTS_PER_SESSION,
        bump_pending_2fa_attempts,
        clear_pending_2fa,
        consume_pending_2fa,
    )
    from apps.core.auth_cookies import set_auth_cookie

    token = str(request.data.get('token', '')).strip()
    user_id, attempts = consume_pending_2fa(request)

    if not token or not user_id:
        return Response({'error': 'Token required'},
                        status=status.HTTP_400_BAD_REQUEST)

    if attempts >= MAX_2FA_ATTEMPTS_PER_SESSION:
        clear_pending_2fa(request)
        logger.warning("2FA handshake locked out after %d attempts (session)", attempts)
        return Response(
            {'error': 'Too many invalid codes. Please log in again.',
             'code': '2fa_locked'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        clear_pending_2fa(request)
        return Response({'error': 'Session expired. Please log in again.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    if not user.is_active:
        clear_pending_2fa(request)
        return Response({'error': 'Account is disabled.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    # Verify token against any confirmed TOTP device
    for device in devices_for_user(user, confirmed=True):
        if device.verify_token(token):
            login(request, user)  # rotates the session key (fixation-safe)
            clear_pending_2fa(request)
            from rest_framework.authtoken.models import Token
            drf_token, _ = Token.objects.get_or_create(user=user)
            response = Response({
                'success': True,
                'message': '2FA verified',
                'key': drf_token.key,
            })
            try:
                set_auth_cookie(response, drf_token.key)
            except Exception:
                pass
            return response

    left = MAX_2FA_ATTEMPTS_PER_SESSION - bump_pending_2fa_attempts(request)
    logger.warning("Invalid 2FA code for user %s (%d attempts left)", user_id, max(left, 0))
    return Response({'error': 'Invalid verification code'},
                    status=status.HTTP_401_UNAUTHORIZED)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def two_factor_backup_codes(request):
    """Generate new backup codes for the user (invalidates old ones)."""
    from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

    # Remove old static devices
    for device in devices_for_user(request.user):
        if hasattr(device, 'plugin') and device.plugin == 'otp_static':
            device.delete()

    # Create new static device with 10 codes
    device = StaticDevice.objects.create(user=request.user, name='backup')
    codes = []
    for _ in range(10):
        import secrets
        code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
        codes.append(code)
        StaticToken.objects.create(device=device, token=code)

    return Response({'codes': codes, 'message': 'Store these safely. Each code can only be used once.'})
