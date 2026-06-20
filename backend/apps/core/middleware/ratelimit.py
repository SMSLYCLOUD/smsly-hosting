"""Rate limiting middleware - Zero Trust hardened."""
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class RateLimitMiddleware:
    """
    Sliding window rate limiter backed by Django cache.
    Protects API endpoints from DDoS/abuse.

    Behavior on cache backend fault is configurable:
    - API_RATE_LIMIT_FAIL_CLOSED=true  -> deny request
    - API_RATE_LIMIT_FAIL_CLOSED=false -> allow request (degraded mode)
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.rate_limit = int(getattr(settings, "API_RATE_LIMIT", 1000))
        self.window = 60  # seconds
        self.fail_closed = bool(
            getattr(settings, "API_RATE_LIMIT_FAIL_CLOSED", False)
        )

    def __call__(self, request):
        if request.path.startswith("/api/"):
            # DRF handles per-user throttling. This middleware is an edge
            # guard for anonymous/unauthenticated request bursts.
            if not (hasattr(request, "user") and request.user.is_authenticated):
                ip = self._get_client_ip(request)
                if not self._check_rate_limit(ip):
                    logger.warning("Rate limit exceeded for IP %s", ip)
                    return JsonResponse(
                        {"error": "Too Many Requests", "retry_after": self.window},
                        status=429,
                    )

        response = self.get_response(request)
        return response

    def _get_client_ip(self, request):
        """
        Use X-Real-IP (set by reverse proxy) or REMOTE_ADDR.
        Never trust unvetted X-Forwarded-For leftmost values.
        """
        real_ip = request.META.get("HTTP_X_REAL_IP")
        if real_ip:
            return real_ip.strip()
        return request.META.get("REMOTE_ADDR", "0.0.0.0")

    def _check_rate_limit(self, ip):
        """Return True when request is allowed."""
        if self.rate_limit <= 0:
            return True

        key = f"ratelimit:{ip}:{int(time.time() // self.window)}"
        try:
            # Initialize key atomically if missing; otherwise increment.
            count = 1 if cache.add(key, 1, timeout=self.window) else cache.incr(key, 1)
        except Exception:
            action = "denying" if self.fail_closed else "allowing"
            logger.exception(
                "Rate limit cache operation failed; %s request (%s mode)",
                action,
                "fail-closed" if self.fail_closed else "fail-open",
            )
            return not self.fail_closed

        return count <= self.rate_limit
