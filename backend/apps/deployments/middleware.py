import asyncio
import contextlib
import logging

from channels.db import database_sync_to_async
from django.conf import settings

logger = logging.getLogger(__name__)

# Redis errors that can occur during WebSocket operations
_REDIS_ERRORS = (
    TimeoutError,
    ConnectionError,
    OSError,
)


class RedisResilientMiddleware:
    """
    ASGI middleware that wraps WebSocket consumers to handle Redis failures
    gracefully. When Redis is unavailable (timeout, connection refused, etc.),
    the consumer is closed with a specific code instead of crashing the ASGI
    worker.

    This prevents a single Redis hiccup from tearing down all active WebSocket
    connections and ensures the backend process remains healthy.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "websocket":
            return await self.app(scope, receive, send)

        async def wrapped_receive():
            try:
                return await receive()
            except _REDIS_ERRORS as exc:
                logger.warning("Redis error during WS receive: %s", exc)
                return {"type": "websocket.disconnect", "code": 4503}

        async def wrapped_send(message):
            try:
                return await send(message)
            except _REDIS_ERRORS as exc:
                logger.warning("Redis error during WS send: %s", exc)
                # Suppress send errors — the connection is already closing

        try:
            return await self.app(scope, wrapped_receive, wrapped_send)
        except _REDIS_ERRORS as exc:
            logger.error("Redis error in WS consumer: %s", exc)
            # Attempt to close the WebSocket gracefully
            with contextlib.suppress(Exception):
                await send({"type": "websocket.close", "code": 4503})
            return None


class DynamicAllowedHostsASGIMiddleware:
    """
    ASGI middleware that dynamically adds valid hosts to ALLOWED_HOSTS
    during the WebSocket handshake.  This mirrors DynamicAllowedHostsMiddleware
    for HTTP but works at the ASGI level so WebSocket connections are not
    rejected by AllowedHostsOriginValidator after a domain config change.

    Note: ``is_valid_host()`` performs synchronous DB queries.  We wrap it
    in ``database_sync_to_async`` so the event loop is not blocked during
    the async handshake path.
    """
    def __init__(self, app):
        self.app = app

    @database_sync_to_async
    def _is_valid_host(self, host: str) -> bool:
        from apps.deployments.patching import is_valid_host
        return is_valid_host(host)

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            host = ""
            for raw_name, raw_value in scope.get("headers", []):
                if raw_name == b"host":
                    try:
                        host = raw_value.decode("latin-1").split(":")[0]
                    except UnicodeDecodeError:
                        pass
                    break
            if host and host not in settings.ALLOWED_HOSTS:
                try:
                    if await self._is_valid_host(host):
                        settings.ALLOWED_HOSTS.append(host)
                        logger.info(
                            "DynamicAllowedHostsASGIMiddleware: whitelisted %s", host
                        )
                except Exception as exc:
                    logger.warning("Dynamic WS host patching failed: %s", exc)
        return await self.app(scope, receive, send)


class DynamicAllowedHostsMiddleware:
    """
    Dynamically patches ALLOWED_HOSTS if an incoming request's host matches
    the domain configured in PlatformConfig. This completely solves
    multi-process stale state where one Gunicorn worker updates the DB
    but other workers haven't reloaded ALLOWED_HOSTS yet.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.META.get('HTTP_HOST', '').split(':')[0]
        if host and host not in settings.ALLOWED_HOSTS:
            try:
                from apps.deployments.patching import (
                    is_valid_host,
                    patch_runtime_settings,
                )
                if is_valid_host(host):
                    settings.ALLOWED_HOSTS.append(host)
                    logger.info("DynamicAllowedHostsMiddleware: instantly whitelisted valid domain %s", host)
                else:
                    # Still run the standard sync for origin patching just in case
                    patch_runtime_settings()
            except Exception as e:
                logger.warning("Dynamic host patching failed: %s", e)

        return self.get_response(request)

import hashlib  # noqa: E402
from urllib.parse import parse_qs  # noqa: E402

from channels.db import database_sync_to_async  # noqa: E402
from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.db import close_old_connections  # noqa: E402


@database_sync_to_async
def get_user_from_token(token_key: str):
    """
    Identifies the user from a standard DRF token key.
    Includes close_old_connections to prevent DB stalling in long-lived WS connections.
    """
    try:
        from rest_framework.authtoken.models import Token
        # 1. Try standard DRF Token
        token = Token.objects.select_related('user').get(key=token_key)
        if token.user.is_active:
            return token.user
    except Token.DoesNotExist:
        # 2. Try custom APIToken (for CLI-style access)
        try:
            from apps.deployments.api_token_auth import APIToken
            # APIToken uses a SHA-256 hash
            token_hash = hashlib.sha256(token_key.encode()).hexdigest()
            api_token = APIToken.objects.select_related('user').get(
                token_hash=token_hash, is_active=True
            )
            if api_token.user.is_active:
                return api_token.user
        except (ImportError, Exception):
            pass
    return AnonymousUser()

class QueryStringAuthMiddleware:
    """
    Custom middleware to authenticate users for WebSocket connections.

    Accepts the credential in two transports, in this order of precedence:

    1. ``?token=...`` query-string parameter — kept for backward
       compatibility with xterm.js and CLI-style integrations that
       cannot easily send HTTP headers from a browser.
    2. The HttpOnly auth cookie set by the login view
       (``__Host-auth_token`` in production, ``auth_token`` in dev).
       The browser attaches this cookie automatically to the WS
       upgrade request, so the frontend does not have to put the
       long-lived DRF token in the URL — query strings are recorded
       in proxy access logs, browser history, and the ``Referer``
       header of cross-origin requests, and a long-lived DRF token
       must never appear in a URL.

    The DRF side has the equivalent
    :class:`apps.core.auth.CookieAwareTokenAuthentication` for HTTP
    endpoints; this middleware mirrors that behaviour for Channels.
    """

    # Match either the production (``__Host-`` prefixed, HTTPS-only) or
    # the development cookie name. The ``__Host-`` prefix REQUIRES
    # ``Secure``, ``Path=/``, and no ``Domain`` attribute, so the
    # browser refuses to set it on plain HTTP. In dev the plain
    # ``auth_token`` name is used instead.
    _COOKIE_NAMES = ('__Host-auth_token', 'auth_token')

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 1. Query-string token (backward compat for xterm.js / CLI clients).
        query_string = scope.get('query_string', b'').decode('utf-8')
        query_params = parse_qs(query_string)
        token_key = query_params.get('token', [None])[0]

        # 2. HttpOnly auth cookie (the path the React frontend actually uses).
        if not token_key:
            token_key = self._extract_cookie_token(scope)

        # 3. Authenticate if a token was found in either transport.
        if token_key:
            close_old_connections()
            scope['user'] = await get_user_from_token(token_key)

        return await self.app(scope, receive, send)

    @classmethod
    def _extract_cookie_token(cls, scope):
        """Return the auth token from the WS upgrade's ``Cookie`` header.

        Returns ``None`` if the header is missing, malformed, or carries
        no recognized auth cookie. The header value is parsed permissively
        (latin-1, which is the wire encoding for HTTP/1.1 headers) so a
        non-ASCII cookie name does not break the WS upgrade entirely —
        it just falls through to ``None`` and the connection is closed
        by the consumer as unauthenticated.
        """
        for raw_name, raw_value in scope.get('headers', []):
            if raw_name.lower() != b'cookie':
                continue
            try:
                header = raw_value.decode('latin-1')
            except UnicodeDecodeError:
                return None
            for chunk in header.split(';'):
                name, sep, value = chunk.strip().partition('=')
                if not sep:
                    continue
                if name in cls._COOKIE_NAMES:
                    return value
            # Cookie header was present but carried no recognized
            # auth cookie — do not fall through to a different header.
            return None
        return None
