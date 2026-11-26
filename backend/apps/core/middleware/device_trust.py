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

    def _resolve_token_user(self, request):
        """Resolve user from DRF auth cookie, Token, APIToken, or APIKey header
        when Django's AuthenticationMiddleware left request.user anonymous.
        """
        import hashlib
        from django.contrib.auth.hashers import check_password
        from django.utils import timezone

        auth_header = str(request.META.get("HTTP_AUTHORIZATION", "")).strip()
        if auth_header:
            scheme, _, raw_token = auth_header.partition(" ")
            scheme_lower = scheme.lower()
            raw_token = raw_token.strip()
            if scheme_lower == "bearer" and raw_token.startswith("smsly_"):
                try:
                    from apps.core.models.api_token import APIToken
                    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                    api_tok = APIToken.objects.select_related("user").filter(
                        token_hash=token_hash, is_active=True, user__is_active=True,
                    ).first()
                    if api_tok:
                        return api_tok.user
                except Exception:
                    return None
            elif scheme_lower == "bearer" and raw_token.startswith("sk_"):
                try:
                    from apps.core.models import APIKey
                    now = timezone.now()
                    keys = APIKey.objects.filter(
                        prefix=raw_token[:8], user__is_active=True,
                    ).exclude(
                        expires_at__isnull=False, expires_at__lt=now,
                    ).select_related("user")
                    for api_key in keys:
                        if check_password(raw_token, api_key.key_hash):
                            return api_key.user
                except Exception:
                    return None
            elif scheme_lower in ("token", "bearer") and raw_token:
                try:
                    from rest_framework.authtoken.models import Token
                    tok = Token.objects.select_related("user").filter(
                        key=raw_token, user__is_active=True,
                    ).first()
                    if tok:
                        return tok.user
                except Exception:
                    return None
            return None

        # Fallback: HttpOnly auth cookie (CookieAwareTokenAuthentication path)
        try:
            from apps.core.auth_cookies import get_cookie_token
            from rest_framework.authtoken.models import Token
            cookie_token = get_cookie_token(request)
            if cookie_token:
                tok = Token.objects.select_related("user").filter(
                    key=cookie_token, user__is_active=True,
                ).first()
                if tok:
                    return tok.user
        except Exception:
            return None
        return None

    def __call__(self, request):
        # Skip for exempt routes
        path = request.path
        if any(path.startswith(route) for route in self.exempt_routes):
            return self.get_response(request)

        # Check if enforcement is enabled (lazy-load PlatformConfig)
        try:
            from apps.deployments.models.core import PlatformConfig
            config = PlatformConfig.load()
            if not getattr(config, 'enforce_device_trust', False):
                return self.get_response(request)
        except Exception:
            # If we can't load config, don't block — fail open
            return self.get_response(request)

        # Resolve effective user: session user first, then DRF token/cookie-token.
        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            user = self._resolve_token_user(request)
        if not user or not getattr(user, 'is_authenticated', False):
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
            from apps.deployments.models.core import TrustedDevice
            device = TrustedDevice.objects.filter(
                user=user,
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
