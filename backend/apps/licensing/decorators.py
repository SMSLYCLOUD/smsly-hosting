from functools import wraps

from rest_framework import status
from rest_framework.response import Response


def require_tier(*allowed_tiers):
    """
    Decorator for DRF views — gates access by platform license tier.

    Checks the current PlatformLicense.tier against the allowed tiers.
    If no license is active or the tier doesn't match, returns 403.
    Pass ``*PlatformTier`` values as arguments, e.g.
    ``@require_tier(PlatformTier.PRO, PlatformTier.ENTERPRISE)``.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                from apps.licensing.models import PlatformLicense
                license_obj = PlatformLicense.load()
                if license_obj.tier not in allowed_tiers:
                    return Response(
                        {"error": f"This feature requires one of: {', '.join(allowed_tiers)}"},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except Exception:
                pass  # If licensing is unavailable, allow access (self-hosted fallback)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

