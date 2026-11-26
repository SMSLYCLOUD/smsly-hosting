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
        from apps.core.patching import is_valid_host
        return is_valid_host(host)

    async def __call__(self, scope, receive, send):
        # Only patch ALLOWED_HOSTS for HTTP requests; WebSocket origin checks
        # are enforced strictly by ControlPlaneOriginValidator without mutating
        # ALLOWED_HOSTS with tenant Service/Addon/Domain hostnames.
        if scope.get("type") == "http":
            host = ""
            for raw_name, raw_value in scope.get("headers", []):
                if raw_name.lower() == b"host":
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


class ControlPlaneOriginValidator:
    """Strict WebSocket Origin validator for control-plane endpoints.

    When an ``Origin`` header is present on the WebSocket upgrade request, it
    must EITHER be explicitly listed in ``settings.CORS_ALLOWED_ORIGINS`` OR
    its hostname must strictly equal an exact control-plane host (never a
    wildcard ``*.<DOMAIN>`` or tenant domain) AND match the request ``Host``
    header.
    """

    def __init__(self, app):
        self.app = app

    @database_sync_to_async
    def _is_control_plane_host(self, hostname: str) -> bool:
        from apps.core.patching import is_control_plane_host
        return is_control_plane_host(hostname)

    @staticmethod
    def _extract_hostname(host_header: str) -> str:
        raw = (host_header or "").strip()
        if not raw:
            return ""
        if raw.startswith("["):
            end = raw.find("]")
            if end != -1:
                return raw[1:end].strip().lower().rstrip(".")
        return raw.rsplit(":", 1)[0].strip().lower().rstrip(".")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "websocket":
            return await self.app(scope, receive, send)

        origin_raw = ""
        host_raw = ""
        for raw_name, raw_value in scope.get("headers", []):
            name_lower = raw_name.lower()
            if name_lower == b"origin" and not origin_raw:
                with contextlib.suppress(UnicodeDecodeError):
                    origin_raw = raw_value.decode("latin-1").strip()
            elif name_lower == b"host" and not host_raw:
                with contextlib.suppress(UnicodeDecodeError):
                    host_raw = raw_value.decode("latin-1").strip()

        if origin_raw:
            from urllib.parse import urlparse

            parsed = urlparse(origin_raw)
            scheme = (parsed.scheme or "").lower()
            netloc = (parsed.netloc or "").lower()
            origin_hostname = (parsed.hostname or "").strip().strip("[]").lower().rstrip(".")

            if scheme not in ("http", "https") or not netloc or not origin_hostname:
                logger.warning("ControlPlaneOriginValidator: rejected malformed Origin=%r", origin_raw)
                await send({"type": "websocket.close"})
                return None

            origin_base = f"{scheme}://{netloc}"
            allowed_cors = {
                str(o).strip().rstrip("/").lower()
                for o in (getattr(settings, "CORS_ALLOWED_ORIGINS", ()) or ())
                if o
            }
            if origin_base not in allowed_cors:
                host_hostname = self._extract_hostname(host_raw)
                if (
                    not host_hostname
                    or origin_hostname != host_hostname
                    or not await self._is_control_plane_host(origin_hostname)
                ):
                    logger.warning(
                        "ControlPlaneOriginValidator: rejected WS Origin=%r Host=%r",
                        origin_raw, host_raw,
                    )
                    await send({"type": "websocket.close"})
                    return None

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
                from apps.core.patching import (
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

import hashlib
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db import close_old_connections


class SameOriginCookieWebsocketValidator:
    """
    Reject cross-origin cookie/session-authenticated WebSocket handshakes when
    no explicit token was supplied via query string, Authorization header, or
    Sec-WebSocket-Protocol subprotocol.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _extract_hostname(raw_value: str) -> str:
        from urllib.parse import urlparse
        raw = str(raw_value or '').strip()
        if not raw or raw.lower() == 'null':
            return ''
        if '://' not in raw:
            raw = f'http://{raw}'
        try:
            return (urlparse(raw).hostname or '').strip().lower()
        except Exception:
            return ''

    @staticmethod
    def _has_explicit_ws_token(scope) -> bool:
        query_string = scope.get('query_string', b'').decode('utf-8', errors='ignore')
        if parse_qs(query_string).get('token', [None])[0]:
            return True
        for raw_name, raw_value in scope.get('headers', []):
            if raw_name.lower() == b'authorization':
                val = raw_value.decode('latin-1', errors='ignore').strip()
                scheme, _, tok = val.partition(' ')
                if scheme.lower() in ('token', 'bearer') and tok.strip():
                    return True
        for proto in (scope.get('subprotocols') or []):
            if proto and proto != 'token':
                return True
        return False

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'websocket' and not self._has_explicit_ws_token(scope):
            origin_raw = None
            host_raw = ''
            for raw_name, raw_value in scope.get('headers', []):
                lname = raw_name.lower()
                if lname == b'origin':
                    origin_raw = raw_value.decode('latin-1', errors='ignore')
                elif lname == b'host':
                    host_raw = raw_value.decode('latin-1', errors='ignore')

            if origin_raw is not None:
                origin_host = self._extract_hostname(origin_raw)
                host_host = self._extract_hostname(host_raw)
                if not origin_host or not host_host or origin_host != host_host:
                    logger.warning(
                        "SameOriginCookieWebsocketValidator: rejected cross-origin cookie WS Origin=%r Host=%r",
                        origin_raw, host_raw,
                    )
                    await send({"type": "websocket.close", "code": 4003})
                    return None

        return await self.app(scope, receive, send)


def _resolve_ws_token_sync(token_key: str):
    """Synchronously resolve a WS token key into ``(user, api_token)``."""
    from django.contrib.auth import get_user_model
    from django.core.cache import cache
    from django.utils import timezone
    from rest_framework.authtoken.models import Token

    if isinstance(token_key, str) and token_key.startswith('wst_'):
        ticket_digest = hashlib.sha256(token_key.encode()).hexdigest()
        cached_user_id = cache.get(f'ws_ticket:{ticket_digest}')
        if cached_user_id is not None:
            try:
                user = get_user_model().objects.get(id=cached_user_id, is_active=True)
                return user, None
            except Exception:
                pass
        return AnonymousUser(), None

    try:
        token = Token.objects.select_related('user').get(key=token_key)
        if token.user.is_active:
            return token.user, None
    except Token.DoesNotExist:
        try:
            from apps.core.models.api_token import APIToken
            token_hash = hashlib.sha256(token_key.encode()).hexdigest()
            api_token = APIToken.objects.select_related('user').get(
                token_hash=token_hash, is_active=True
            )
            expires_at = getattr(api_token, 'expires_at', None)
            if expires_at is not None and expires_at <= timezone.now():
                return AnonymousUser(), None
            if api_token.user.is_active:
                return api_token.user, api_token
        except (ImportError, Exception):
            pass
    return AnonymousUser(), None


@database_sync_to_async
def get_credential_from_token(token_key: str):
    """Identify ``(user, api_token)`` from a DRF Token or APIToken."""
    return _resolve_ws_token_sync(token_key)


@database_sync_to_async
def get_user_from_token(token_key: str):
    """
    Identifies the user from a standard DRF token key.
    Includes close_old_connections to prevent DB stalling in long-lived WS connections.
    """
    user, _api_token = _resolve_ws_token_sync(token_key)
    return user


@database_sync_to_async
def get_user_from_session_key(session_key: str):
    """
    Authenticate a user via Django session key.
    Verifies session expiration, auth session hash, and 2FA completion.
    """
    try:
        from django.contrib.auth import get_user_model
        from django.contrib.sessions.models import Session
        from django.utils import timezone
        from django.utils.crypto import constant_time_compare
        from apps.core.auth_2fa import user_requires_2fa

        session = Session.objects.get(
            session_key=session_key,
            expire_date__gt=timezone.now(),
        )
        session_data = session.get_decoded()
        user_id = session_data.get('_auth_user_id')
        if user_id is None:
            return AnonymousUser()

        User = get_user_model()
        user = User.objects.get(pk=user_id, is_active=True)

        session_hash = str(session_data.get('_auth_user_hash') or '')
        if not session_hash or not constant_time_compare(session_hash, user.get_session_auth_hash()):
            return AnonymousUser()

        if user_requires_2fa(user) and not session_data.get('2fa_verified'):
            return AnonymousUser()

        return user
    except Exception:
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
    _SESSION_COOKIE_NAMES = ('__Host-sessionid', 'sessionid', '__sessionid')

    def __init__(self, app):
        self.app = app

    async def _authenticate_ws_token(self, token_key: str):
        return await get_credential_from_token(token_key)

    async def __call__(self, scope, receive, send):
        # 1. Query-string token (backward compat for xterm.js / CLI clients).
        query_string = scope.get('query_string', b'').decode('utf-8')
        query_params = parse_qs(query_string)
        token_key = query_params.get('token', [None])[0]

        # 2. Authorization: Token/Bearer <tok> header (used by master->worker relays).
        if not token_key:
            token_key = self._extract_auth_header_token(scope)

        # 3. Sec-WebSocket-Protocol subprotocol (['token', '<tok>'] or 'token.<tok>').
        if not token_key:
            token_key = self._extract_subprotocol_token(scope)

        # 4. HttpOnly auth cookie (the path the React frontend actually uses).
        if not token_key:
            token_key = self._extract_cookie_token(scope)

        # 5. Authenticate if a token was found in any transport.
        if token_key:
            close_old_connections()
            user, api_token = await self._authenticate_ws_token(token_key)
            scope['user'] = user
            scope['api_token'] = api_token
        else:
            scope.setdefault('api_token', None)

        # 6. Fallback: try Django session cookie if user is still anonymous.
        #    This handles the case where the user authenticated via the allauth
        #    login flow (session-based) and the auth_token cookie is stale or
        #    missing.  Django's session cookie is the canonical session
        #    credential; the WS upgrade request carries it automatically.
        user = scope.get('user')
        if not user or getattr(user, 'is_anonymous', True):
            session_key = self._extract_session_cookie(scope)
            if session_key:
                close_old_connections()
                scope['user'] = await get_user_from_session_key(session_key)
                scope['api_token'] = None

        return await self.app(scope, receive, send)

    @classmethod
    def _extract_auth_header_token(cls, scope):
        for raw_name, raw_value in scope.get('headers', []):
            if raw_name.lower() != b'authorization':
                continue
            try:
                val = raw_value.decode('latin-1').strip()
            except UnicodeDecodeError:
                continue
            scheme, _, tok = val.partition(' ')
            if scheme.lower() in ('token', 'bearer') and tok.strip():
                return tok.strip()
        return None

    @classmethod
    def _extract_subprotocol_token(cls, scope):
        for proto in (scope.get('subprotocols') or []):
            if not proto:
                continue
            if isinstance(proto, bytes):
                proto = proto.decode('latin-1', errors='ignore')
            if proto.startswith('token.'):
                tok = proto[len('token.'):].strip()
                if tok:
                    return tok
            elif proto != 'token':
                tok = proto.strip()
                if tok:
                    return tok
        return None

    @classmethod
    def _extract_cookie_by_priority(cls, scope, names: tuple[str, ...]):
        """Scan ALL ``Cookie`` headers (HTTP/2 may send multiple) and return
        the value for the highest-priority cookie name in *names*.
        """
        found: dict[str, str] = {}
        for raw_name, raw_value in scope.get('headers', []):
            if raw_name.lower() != b'cookie':
                continue
            try:
                header = raw_value.decode('latin-1')
            except UnicodeDecodeError:
                continue
            for chunk in header.split(';'):
                name, sep, value = chunk.strip().partition('=')
                if not sep:
                    continue
                if name in names and name not in found:
                    found[name] = value
        for candidate in names:
            if candidate in found:
                return found[candidate]
        return None

    @classmethod
    def _extract_cookie_token(cls, scope):
        """Return the auth token from the WS upgrade's ``Cookie`` headers,
        prioritizing ``__Host-auth_token`` over ``auth_token``.
        """
        return cls._extract_cookie_by_priority(scope, cls._COOKIE_NAMES)

    @classmethod
    def _extract_session_cookie(cls, scope):
        """Return the Django session key from the WS upgrade's ``Cookie`` headers,
        prioritizing ``__Host-sessionid`` over ``sessionid`` / ``__sessionid``.
        """
        return cls._extract_cookie_by_priority(scope, cls._SESSION_COOKIE_NAMES)
