import logging
from functools import wraps

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def require_tier(*allowed_tiers):
    """
    Decorator for DRF views — gates access by platform license tier.

    Checks ``PlatformLicense.is_valid``, ``PlatformLicense.expires_at``, and
    ``PlatformLicense.tier`` against the allowed tiers. Fails closed (403)
    when the license is invalid, expired, mismatched, or verification fails.
    Pass ``*PlatformTier`` values as arguments, e.g.
    ``@require_tier(PlatformTier.PRO, PlatformTier.ENTERPRISE)``.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            try:
                from apps.licensing.models import PlatformLicense
                license_obj = PlatformLicense.load()
                if not license_obj.is_valid:
                    return Response(
                        {"error": "A valid platform license is required for this feature."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                if license_obj.expires_at and license_obj.expires_at <= timezone.now():
                    return Response(
                        {"error": "Platform license has expired."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                if license_obj.tier not in allowed_tiers:
                    return Response(
                        {"error": f"This feature requires one of: {', '.join(allowed_tiers)}"},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except Exception:
                logger.exception("License tier check failed; failing closed")
                return Response(
                    {"error": "License verification unavailable."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return view_func(*args, **kwargs)
        return wrapper
    return decorator

