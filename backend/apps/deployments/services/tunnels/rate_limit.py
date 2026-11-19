# pylint: disable=logging-fstring-interpolation
"""Rate Limit module."""
# pylint: disable=broad-exception-caught
"""
Rate limiting utilities for SMSLY API endpoints.

Uses Django's cache framework for distributed rate limiting.
"""

import logging
import time
from functools import wraps

from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class RateLimiter:  # pylint: disable=too-few-public-methods
    """
    Token bucket rate limiter using Redis/cache.

    Supports different limits for authenticated vs anonymous users.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_minute_anon: int = 20,
        burst_multiplier: float = 1.5
    ):
        self.rpm = requests_per_minute
        self.rpm_anon = requests_per_minute_anon
        self.burst = burst_multiplier

    def _get_client_id(self, request) -> str:
        """Get unique client identifier without trusting spoofed headers."""
        if request.user.is_authenticated:
            return f"user:{request.user.id}"

        from apps.core.rate_limiting import get_trusted_client_ip

        ip = get_trusted_client_ip(request, default="unknown")
        return f"ip:{ip}"

    def _get_limit(self, request) -> int:
        """Get rate limit based on auth status."""
        if request.user.is_authenticated:
            return self.rpm
        return self.rpm_anon

    def is_allowed(self, request) -> tuple:
        """
        Check if request is allowed under an atomic fixed-window rate limit.

        Returns:
            (allowed: bool, info: dict with limit details)
        """
        client_id = self._get_client_id(request)
        limit = self._get_limit(request)
        burst_limit = max(1, int(limit * self.burst))

        bucket = int(time.time() // 60)
        key = f"ratelimit:{client_id}:{bucket}"
        reset_time = (bucket + 1) * 60
        retry_after = max(1, int(reset_time - time.time()))

        try:
            if cache.add(key, 1, timeout=120):
                current_count = 1
            else:
                try:
                    current_count = int(cache.incr(key))
                except ValueError:
                    cache.set(key, 1, timeout=120)
                    current_count = 1

            allowed = current_count <= burst_limit
            remaining = max(0, burst_limit - current_count) if allowed else 0

            return allowed, {
                'limit': limit,
                'remaining': remaining,
                'reset': reset_time,
                'retry_after': retry_after if not allowed else 0,
            }

        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("Rate limit check failed: %s", e)
            # Fail open on cache errors to avoid blocking requests
            return True, {'limit': limit, 'remaining': limit, 'reset': 0}


def rate_limit(requests_per_minute: int = 60, anon_rpm: int = 20):
    """
    Decorator to apply rate limiting to a view.

    Usage:
        @rate_limit(requests_per_minute=30)
        def my_view(request):
            ...
    """
    limiter = RateLimiter(
        requests_per_minute=requests_per_minute,
        requests_per_minute_anon=anon_rpm
    )

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            allowed, info = limiter.is_allowed(request)

            # Add rate limit headers to all responses
            def add_headers(response):
                response['X-RateLimit-Limit'] = str(info['limit'])
                response['X-RateLimit-Remaining'] = str(info['remaining'])
                response['X-RateLimit-Reset'] = str(info['reset'])
                return response

            if not allowed:
                response = Response(
                    {
                        'error': 'Rate limit exceeded',
                        'retry_after': info['retry_after']
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )
                response['Retry-After'] = str(info['retry_after'])
                return add_headers(response)

            response = view_func(request, *args, **kwargs)
            return add_headers(response)

        return wrapped
    return decorator
