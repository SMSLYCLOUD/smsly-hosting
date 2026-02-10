"""Rate limiting middleware — Zero Trust hardened."""
import time
import logging
from django.core.cache import cache
from django.http import JsonResponse
from django.conf import settings

logger = logging.getLogger(__name__)


class RateLimitMiddleware:
    """
    Sliding window rate limiter backed by Django cache (Redis).
    Protects API endpoints from DDoS/Abuse.

    ZH-004 FIX: Fails CLOSED when cache is unreachable.
    ZH-005 FIX: Uses REMOTE_ADDR (set by nginx X-Real-IP) instead of
                spoofable X-Forwarded-For leftmost IP.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.rate_limit = getattr(settings, 'API_RATE_LIMIT', 1000)
        self.window = 60  # seconds

    def __call__(self, request):
        if request.path.startswith('/api/'):
            ip = self._get_client_ip(request)
            if not self._check_rate_limit(ip):
                logger.warning("Rate limit exceeded for IP %s", ip)
                return JsonResponse(
                    {"error": "Too Many Requests", "retry_after": self.window},
                    status=429
                )

        response = self.get_response(request)
        return response

    def _get_client_ip(self, request):
        """
        ZH-005 FIX: Use X-Real-IP (set by nginx from $remote_addr) or
        fall back to REMOTE_ADDR. Never trust X-Forwarded-For leftmost IP
        since it's trivially spoofable.
        """
        # X-Real-IP is set by nginx from the actual TCP connection IP
        real_ip = request.META.get('HTTP_X_REAL_IP')
        if real_ip:
            return real_ip.strip()

        # Fallback: direct connection IP (safe in a non-proxied setup)
        return request.META.get('REMOTE_ADDR', '0.0.0.0')

    def _check_rate_limit(self, ip):
        """
        ZH-004 FIX: Fail-closed — if cache is unreachable, DENY the request
        rather than allowing unlimited traffic through.
        """
        key = f"ratelimit:{ip}:{int(time.time() // self.window)}"
        try:
            # Atomic increment
            count = cache.incr(key, 1)
        except ValueError:
            # Key didn't exist, set it to 1
            try:
                cache.set(key, 1, timeout=self.window)
            except Exception:
                # ZH-004: Cache write failed — fail closed
                logger.error("Rate limit cache SET failed — denying request (fail-closed)")
                return False
            count = 1
        except Exception:
            # ZH-004: Cache is unreachable — fail closed
            logger.error("Rate limit cache INCR failed — denying request (fail-closed)")
            return False

        return count <= self.rate_limit
