"""Shared utilities for deployment WebSocket consumers."""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from channels.db import database_sync_to_async
from django.core.cache import cache

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

logger = logging.getLogger(__name__)

# Redis errors that can occur during channel layer operations
_REDIS_WS_ERRORS = (
    TimeoutError,
    ConnectionError,
    OSError,
)


@database_sync_to_async
def verify_deployment_ownership(user, deployment_id) -> bool:
    """Check whether *user* owns the deployment (directly or via team)."""
    from django.db.models import Q
    from apps.deployments.models import Deployment
    try:
        return Deployment.objects.filter(
            Q(service__owner=user) |
            Q(service__project__team__members__user=user),
            id=deployment_id,
        ).exists()
    except Exception:
        return False


@database_sync_to_async
def authenticate_ws_token(token_key: str) -> AbstractUser | None:
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
        except (Token.DoesNotExist, APIToken.DoesNotExist):
            pass
        except Exception:
            pass  # fallback for unexpected errors

    cache.set(cache_key, True, 300)
    return None


def get_websocket_subprotocol(scope: dict) -> str | None:
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
