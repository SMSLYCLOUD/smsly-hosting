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
                    # Match the envelope used by smsly_exception_handler so
                    # clients have one shape for all 429s regardless of
                    # which layer (DRF throttle vs this middleware) returned
                    # the response. Retry-After header is RFC 6585 required.
                    response = JsonResponse(
                        {
                            "error": "Too Many Requests",
                            "code": "throttled",
                            "status": 429,
                        },
                        status=429,
                    )
                    response["Retry-After"] = str(self.window)
                    return response

        response = self.get_response(request)
        return response

    def _get_client_ip(self, request):
        """
        Use X-Real-IP (set by reverse proxy) or REMOTE_ADDR.
        Never trust unvetted X-Forwarded-For leftmost values.

        SECURITY: X-Real-IP is only trusted when the direct TCP connection
        (REMOTE_ADDR) comes from a known proxy IP in TRUSTED_PROXY_IPS.
        Otherwise REMOTE_ADDR is used regardless of any X-Real-IP header,
        preventing clients from spoofing their IP to evade rate limits.
        """
        trusted_proxies = getattr(settings, "TRUSTED_PROXY_IPS", []) or []
        remote_addr = request.META.get("REMOTE_ADDR", "0.0.0.0")
        real_ip = request.META.get("HTTP_X_REAL_IP")

        if real_ip and remote_addr in trusted_proxies:
            return real_ip.strip()
        return remote_addr

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
