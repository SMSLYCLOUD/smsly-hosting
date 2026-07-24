"""Shared utilities for deployment WebSocket consumers."""
import hashlib
import logging

from channels.db import database_sync_to_async
from django.core.cache import cache

from apps.deployments.utils import log_event

logger = logging.getLogger(__name__)

# Redis errors that can occur during channel layer operations
_REDIS_WS_ERRORS = (
    TimeoutError,
    ConnectionError,
    OSError,
)


@database_sync_to_async
def authenticate_ws_token(token_key: str):
    """
    Validate WS token against DRF Tokens and APITokens, returning the active User if valid.
    """
    from rest_framework.authtoken.models import Token

    if not token_key or not isinstance(token_key, str):
        return None

    cache_key = f'invalid_token:{hashlib.sha256(token_key.encode()).hexdigest()}'
    if cache.get(cache_key):
        return None

    try:
        token = Token.objects.select_related('user').get(key=token_key)
        if token.user.is_active:
            return token.user
    except Token.DoesNotExist:
        try:
            from apps.deployments.models.api_token import APIToken
            token_hash = hashlib.sha256(token_key.encode()).hexdigest()
            api_token = APIToken.objects.select_related('user').get(
                token_hash=token_hash, is_active=True
            )
            if api_token.user.is_active:
                return api_token.user
        except Exception:
            pass

    cache.set(cache_key, True, 300)
    return None


def get_websocket_subprotocol(scope):
    """
    Return the subprotocol to negotiate during WebSocket accept().
    When XtermConsole or a browser client requests subprotocols (e.g. ['token', '<wsToken>']),
    RFC 6455 requires the server to echo the accepted subprotocol in Sec-WebSocket-Protocol.
    Failing to do so causes the browser to close the connection immediately with code 1006.
    """
    subprotocols = scope.get('subprotocols') or []
    if 'token' in subprotocols:
        return 'token'
    for p in subprotocols:
        if p and p.startswith('token.'):
            return p
    return subprotocols[0] if subprotocols else None
