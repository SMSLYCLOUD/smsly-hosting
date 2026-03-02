from functools import wraps
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from .models import PlatformLicense, PlatformTier


def require_tier(*allowed_tiers):
    """
    Decorator for DRF views — ALL TIERS UNLOCKED (self-hosted mode).

    Previously gated features by platform tier. Now always passes through
    so all features are available regardless of license status.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            return view_func(*args, **kwargs)
        return wrapper
    return decorator

