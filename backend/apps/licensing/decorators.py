from functools import wraps
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .models import PlatformLicense, PlatformTier

def require_tier(*allowed_tiers):
    """
    Decorator for DRF views that gates access by platform tier.

    Usage:
        @require_tier('pro', 'enterprise')
        def my_premium_view(request):
            ...

    Supports both function-based views (request as first arg) and class-based methods (self, request...).
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            # Locate request object
            request = None
            for arg in args:
                # Look for object that looks like a Request
                if hasattr(arg, 'user') and hasattr(arg, 'method') and hasattr(arg, 'META'):
                    request = arg
                    break

            if not request:
                # Should not happen for valid views, but fail safe
                return Response(
                    {'error': 'internal_error', 'message': 'Could not determine request context.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Load the license singleton
            try:
                license_obj = PlatformLicense.load()
            except Exception:
                return Response(
                    {'error': 'license_error', 'message': 'Could not load platform license.'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            # Check if current tier is allowed
            current_tier = license_obj.tier

            # If the license is invalid (expired/tampered), treat as Community
            if not license_obj.is_valid:
                current_tier = PlatformTier.COMMUNITY

            if current_tier not in allowed_tiers:
                return Response(
                    {
                        'error': 'upgrade_required',
                        'message': f'This feature requires {allowed_tiers[0].title()} tier or above.',
                        'current_tier': current_tier,
                        'required_tier': allowed_tiers[0],
                        'upgrade_url': '/settings/billing',
                    },
                    status=status.HTTP_402_PAYMENT_REQUIRED,
                )

            return view_func(*args, **kwargs)
        return wrapper
    return decorator
