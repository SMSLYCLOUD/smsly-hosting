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
from rest_framework.throttling import AnonRateThrottle

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
@throttle_classes([AnonRateThrottle])
def two_factor_login(request):
    """
    Verify a 2FA token during login.

    Expects the session has `2fa_user_id` set (from the first login step).
    On success, logs the user in fully.
    """
    from django.contrib.auth import login

    token = request.data.get('token', '')
    user_id = request.session.get('2fa_user_id')

    if not token or not user_id:
        return Response({'error': 'Token required'},
                        status=status.HTTP_400_BAD_REQUEST)

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'Session expired. Please log in again.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    # Verify token against any confirmed TOTP device
    for device in devices_for_user(user, confirmed=True):
        if device.verify_token(token):
            login(request, user)
            del request.session['2fa_user_id']
            return Response({'success': True, 'message': '2FA verified'})

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
