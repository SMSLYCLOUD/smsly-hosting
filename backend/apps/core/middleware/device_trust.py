"""
Device Trust Middleware — Beta feature.

When PlatformConfig.enforce_device_trust is enabled, this middleware:
1. Checks for a X-Device-Token header on authenticated API requests
2. Validates the token against TrustedDevice records
3. Returns 403 with a structured error if the device is unrecognized
4. Allows device registration endpoints and exempt routes through

The middleware is a no-op when enforce_device_trust is disabled (default).
"""
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


class DeviceTrustMiddleware:
    """
    Enforce device trust when enabled in PlatformConfig.

    Exempt routes (device registration, auth, health checks, etc.) are
    always allowed through regardless of enforcement status.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_routes = [
            '/health',
            '/health/',
            '/admin/',
            '/static/',
            '/media/',
            '/accounts/',
            '/api/v1/accounts/',
            '/api/v1/auth/',
            '/api/v1/webhooks/',
            '/api/v1/system/route-recheck/',
            '/api/v1/system/security-status/',
            # Device trust endpoints — must be accessible to register
            '/api/v1/devices/register/',
            '/api/v1/devices/',
            # Templates (public)
            '/api/v1/templates/',
            '/api/v1/integrations/',
            '/api/v1/oauth/',
            '/api/v1/services/check-domain/',
        ]

    def __call__(self, request):
        # Skip for exempt routes
        path = request.path
        if any(path.startswith(route) for route in self.exempt_routes):
            return self.get_response(request)

        # Skip for unauthenticated requests (auth middleware handles those)
        if not hasattr(request, 'user') or not request.user or not request.user.is_authenticated:
            return self.get_response(request)

        # Check if enforcement is enabled (lazy-load PlatformConfig)
        try:
            from apps.deployments.models_core import PlatformConfig
            config = PlatformConfig.load()
            if not getattr(config, 'enforce_device_trust', False):
                return self.get_response(request)
        except Exception:
            # If we can't load config, don't block — fail open
            return self.get_response(request)

        # Check for device token in header
        device_token = request.META.get('HTTP_X_DEVICE_TOKEN', '').strip()
        if not device_token:
            # Also check cookie
            device_token = request.COOKIES.get('device_token', '').strip()

        if not device_token:
            return JsonResponse({
                'error': 'Device registration required',
                'code': 'DEVICE_TRUST_REQUIRED',
                'detail': (
                    'This platform has device trust enforcement enabled (Beta). '
                    'Your device must be registered before accessing this resource. '
                    'Call POST /api/v1/devices/register/ with a device fingerprint '
                    'to obtain a device token, then include it as X-Device-Token header.'
                ),
            }, status=403)

        # Validate the device token
        try:
            from apps.deployments.models_core import TrustedDevice
            device = TrustedDevice.objects.filter(
                user=request.user,
                device_token=device_token,
                is_active=True,
            ).first()

            if not device:
                return JsonResponse({
                    'error': 'Invalid device token',
                    'code': 'DEVICE_NOT_TRUSTED',
                    'detail': (
                        'The provided device token is not recognized or has been revoked. '
                        'Register this device via POST /api/v1/devices/register/.'
                    ),
                }, status=403)

            # Update last_seen
            from django.utils import timezone
            device.last_seen_at = timezone.now()
            device.ip_address = request.META.get('REMOTE_ADDR', '')
            device.user_agent = request.META.get('HTTP_USER_AGENT', '')[:1000]
            device.save(update_fields=['last_seen_at', 'ip_address', 'user_agent'])

        except Exception as exc:
            logger.warning("Device trust check failed: %s", exc)
            # Fail open — don't block on internal errors
            pass

        return self.get_response(request)
