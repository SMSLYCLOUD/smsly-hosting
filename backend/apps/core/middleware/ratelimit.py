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
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.rate_limit = getattr(settings, 'API_RATE_LIMIT', 1000) # requests per minute
        self.window = 60 # seconds

    def __call__(self, request):
        if request.path.startswith('/api/'):
            ip = self._get_client_ip(request)
            if not self._check_rate_limit(ip):
                logger.warning(f"Rate limit exceeded for IP {ip}")
                return JsonResponse(
                    {"error": "Too Many Requests", "retry_after": self.window},
                    status=429
                )

        response = self.get_response(request)
        return response

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def _check_rate_limit(self, ip):
        """
        Check if the IP has exceeded the rate limit.
        Uses a sliding window approach with Redis lists or simple counters.
        For simplicity/performance, we use a fixed window counter here.
        """
        key = f"ratelimit:{ip}:{int(time.time() // self.window)}"
        try:
            # Atomic increment
            count = cache.incr(key, 1)
        except ValueError:
            # Key didn't exist, set it to 1
            cache.set(key, 1, timeout=self.window)
            count = 1

        return count <= self.rate_limit
