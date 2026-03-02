import logging
from django.conf import settings
from django.utils import timezone
from django.http import JsonResponse
from .models import PlatformLicense

logger = logging.getLogger(__name__)

class TierLimitsMiddleware:
    """
    Tier limits middleware — ALL LIMITS DISABLED (self-hosted mode).

    Previously enforced service count, deployment rate, and team member limits
    based on license tier. Now always passes through.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

