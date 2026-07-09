# pylint: disable=logging-fstring-interpolation
"""Storage module."""
# pylint: disable=import-outside-toplevel
# pylint: disable=broad-exception-caught
"""
Redis-backed storage for SMSLY Tunnels.

Replaces in-memory dictionaries with Redis for production persistence.
Falls back to in-memory storage if Redis is unavailable.
"""

import json
import logging

# pylint: disable=unused-import
from django.core.cache import cache

logger = logging.getLogger(__name__)


class TunnelStorage:
    """
    Redis-backed tunnel storage with in-memory fallback.

    Uses Django's cache framework which can be configured to use Redis.
    """

    # Cache key prefixes
    TUNNEL_PREFIX = "tunnel:"
    SUBDOMAIN_PREFIX = "subdomain:"
    LOG_PREFIX = "tunnel_log:"

    # TTL for tunnels (24 hours default, configurable per tier)
    DEFAULT_TTL = 86400  # 24 hours

    def __init__(self):
        self._fallback_tunnels: dict = {}
        self._fallback_subdomains: dict = {}
        self._fallback_logs: dict = {}
        self._use_redis = self._check_redis()

    def _check_redis(self) -> bool:
        """Check if Redis cache is available."""
        try:
            cache.set("_ping", "pong", 1)
            result = cache.get("_ping") == "pong"
            if result:
                logger.info("TunnelStorage: Using Redis cache")
            return result
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("TunnelStorage: Redis unavailable, using in-memory fallback: %s", e)
            return False

    # ==================== TUNNEL OPERATIONS ====================

    def get_tunnel(self, subdomain: str) -> dict | None:
        """Get tunnel by subdomain."""
        if self._use_redis:
            data = cache.get(f"{self.TUNNEL_PREFIX}{subdomain}")
            return json.loads(data) if data else None
        return self._fallback_tunnels.get(subdomain)

    def get_tunnel_by_id(self, tunnel_id: str) -> dict | None:
        """Get tunnel by its ID."""
        # Scan through tunnels (less efficient but works)
        for tunnel in self.list_tunnels():
            if tunnel.get('tunnel_id') == tunnel_id:
                return tunnel
        return None

    def set_tunnel(self, subdomain: str, tunnel: dict, ttl: int | None = None):
        """Store tunnel data."""
        ttl = ttl or self.DEFAULT_TTL
        if self._use_redis:
            cache.set(f"{self.TUNNEL_PREFIX}{subdomain}",
                      json.dumps(tunnel), ttl)
        else:
            self._fallback_tunnels[subdomain] = tunnel

    def delete_tunnel(self, subdomain: str):
        """Delete tunnel."""
        if self._use_redis:
            cache.delete(f"{self.TUNNEL_PREFIX}{subdomain}")
        else:
            self._fallback_tunnels.pop(subdomain, None)

    def list_tunnels(self, user_id: str | None = None) -> list[dict]:
        """List all tunnels, optionally filtered by user."""
        if self._use_redis:
            # Get all tunnel keys (requires django-redis with pattern support)
            try:
                # pylint: disable=import-outside-toplevel
                from django_redis import get_redis_connection
                conn = get_redis_connection("default")
                keys = conn.keys(f"{self.TUNNEL_PREFIX}*")
                tunnels = []
                for key in keys:
                    data = cache.get(
                        key.decode() if isinstance(
                            key, bytes) else key)
                    if data:
                        tunnel = json.loads(data) if isinstance(
                            data, str) else data
                        if user_id is None or tunnel.get('user_id') == user_id:
                            tunnels.append(tunnel)
                return tunnels
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.warning("Redis scan failed, using values: %s", e)
                # Fallback to iterating stored tunnels
                return [t for t in self._fallback_tunnels.values()
                        if user_id is None or t.get('user_id') == user_id]

        return [t for t in self._fallback_tunnels.values()
                if user_id is None or t.get('user_id') == user_id]

    # ==================== SUBDOMAIN OPERATIONS ====================

    def get_subdomain(self, subdomain: str) -> dict | None:
        """Get reserved subdomain."""
        if self._use_redis:
            data = cache.get(f"{self.SUBDOMAIN_PREFIX}{subdomain}")
            return json.loads(data) if data else None
        return self._fallback_subdomains.get(subdomain)

    def set_subdomain(self, subdomain: str, data: dict):
        """Reserve a subdomain (permanent, no TTL)."""
        if self._use_redis:
            cache.set(f"{self.SUBDOMAIN_PREFIX}{subdomain}",
                      json.dumps(data), None)
        else:
            self._fallback_subdomains[subdomain] = data

    def delete_subdomain(self, subdomain: str):
        """Release a subdomain."""
        if self._use_redis:
            cache.delete(f"{self.SUBDOMAIN_PREFIX}{subdomain}")
        else:
            self._fallback_subdomains.pop(subdomain, None)

    def list_subdomains(self, user_id: str | None = None) -> list[dict]:
        """List reserved subdomains."""
        if self._use_redis:
            try:
                # pylint: disable=import-outside-toplevel
                from django_redis import get_redis_connection
                conn = get_redis_connection("default")
                keys = conn.keys(f"{self.SUBDOMAIN_PREFIX}*")
                subdomains = []
                for key in keys:
                    data = cache.get(
                        key.decode() if isinstance(
                            key, bytes) else key)
                    if data:
                        sub = json.loads(data) if isinstance(
                            data, str) else data
                        if user_id is None or sub.get('user_id') == user_id:
                            subdomains.append(sub)
                return subdomains
            except Exception: # pylint: disable=broad-exception-caught
                return [s for s in self._fallback_subdomains.values()
                        if user_id is None or s.get('user_id') == user_id]

        return [s for s in self._fallback_subdomains.values()
                if user_id is None or s.get('user_id') == user_id]

    # ==================== REQUEST LOG OPERATIONS ====================

    def add_request_log(self, tunnel_id: str, log_entry: dict):
        """Add a request log entry."""
        key = f"{self.LOG_PREFIX}{tunnel_id}"
        if self._use_redis:
            try:
                # pylint: disable=import-outside-toplevel
                from django_redis import get_redis_connection
                conn = get_redis_connection("default")
                conn.lpush(key, json.dumps(log_entry))
                conn.ltrim(key, 0, 99)  # Keep last 100
                conn.expire(key, 86400)  # 24 hour TTL
            except Exception: # pylint: disable=broad-exception-caught
                logs = self._fallback_logs.setdefault(tunnel_id, [])
                logs.insert(0, log_entry)
                self._fallback_logs[tunnel_id] = logs[:100]
        else:
            logs = self._fallback_logs.setdefault(tunnel_id, [])
            logs.insert(0, log_entry)
            self._fallback_logs[tunnel_id] = logs[:100]

    def get_request_logs(self, tunnel_id: str, limit: int = 100) -> list[dict]:
        """Get request logs for a tunnel."""
        key = f"{self.LOG_PREFIX}{tunnel_id}"
        if self._use_redis:
            try:
                # pylint: disable=import-outside-toplevel
                from django_redis import get_redis_connection
                conn = get_redis_connection("default")
                logs = conn.lrange(key, 0, limit - 1)
                return [json.loads(entry) for entry in logs]
            except Exception: # pylint: disable=broad-exception-caught
                return self._fallback_logs.get(tunnel_id, [])[:limit]
        return self._fallback_logs.get(tunnel_id, [])[:limit]

    def delete_request_logs(self, tunnel_id: str):
        """Delete request logs for a tunnel."""
        if self._use_redis:
            cache.delete(f"{self.LOG_PREFIX}{tunnel_id}")
        else:
            self._fallback_logs.pop(tunnel_id, None)


# Singleton instance
tunnel_storage = TunnelStorage()
