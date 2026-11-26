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
def verify_deployment_ownership(user, deployment_id, *, require_write: bool = False, api_token=None) -> bool:
    """Check whether *user* owns/can access the deployment (enforcing active/unexpired roles and APIToken scope/projects)."""
    if not user or not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return False
    if api_token is not None:
        required_scope = 'write' if require_write else 'read'
        if not api_token.has_scope(required_scope):
            return False
    from apps.deployments.models import Deployment, Service
    from apps.teams.permissions import user_can_read, user_can_write
    deleted_states = {
        Service.Status.DELETED,
        Service.Status.DELETION_PENDING,
        Service.Status.DELETION_FAILED,
    }
    try:
        dep = (
            Deployment.objects.select_related('service__project__team')
            .exclude(service__status__in=deleted_states)
            .get(id=deployment_id)
        )
        if api_token is not None:
            if not dep.service or not api_token.has_project(dep.service.project_id):
                return False
        if getattr(user, 'is_superuser', False):
            return True
        if require_write:
            return user_can_write(user, dep.service)
        return user_can_read(user, dep.service)
    except Exception:
        return False


def _authenticate_ws_credential_sync(token_key: str):
    """Synchronously validate a WS token against DRF Tokens and APITokens.

    Returns ``(user, api_token)`` where ``api_token`` is the ``APIToken``
    instance (or ``None`` for standard DRF Tokens / failures).
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    from rest_framework.authtoken.models import Token

    if not token_key or not isinstance(token_key, str):
        return None, None

    token_digest = hashlib.sha256(token_key.encode()).hexdigest()

    if token_key.startswith('wst_'):
        cached_user_id = cache.get(f'ws_ticket:{token_digest}')
        if cached_user_id is not None:
            try:
                user = get_user_model().objects.get(id=cached_user_id, is_active=True)
                return user, None
            except Exception:
                pass
        return None, None

    neg_key = f'invalid_token:{token_digest}'
    if cache.get(neg_key):
        return None, None

    pos_key = f'token_user:{token_digest}'
    cached_user_id = cache.get(pos_key)
    if cached_user_id is not None:
        try:
            user = get_user_model().objects.get(id=cached_user_id, is_active=True)
            return user, None
        except Exception:
            cache.delete(pos_key)

    try:
        token = Token.objects.select_related('user').get(key=token_key)
        if token.user.is_active:
            cache.set(pos_key, token.user.id, 5)
            return token.user, None
    except Token.DoesNotExist:
        try:
            from apps.core.models.api_token import APIToken
            api_token = APIToken.objects.select_related('user').get(
                token_hash=token_digest, is_active=True
            )
            expires_at = getattr(api_token, 'expires_at', None)
            if expires_at is not None and expires_at <= timezone.now():
                cache.set(neg_key, True, 300)
                return None, None
            if api_token.user.is_active:
                return api_token.user, api_token
        except (Token.DoesNotExist, APIToken.DoesNotExist):
            pass
        except Exception:
            pass

    cache.set(neg_key, True, 300)
    return None, None


@database_sync_to_async
def authenticate_ws_credential(token_key: str):
    """Validate WS token and return ``(user, api_token)``."""
    return _authenticate_ws_credential_sync(token_key)


@database_sync_to_async
def authenticate_ws_token(token_key: str) -> AbstractUser | None:
    """
    Validate WS token against DRF Tokens and APITokens, returning the active User if valid.
    """
    user, _api_token = _authenticate_ws_credential_sync(token_key)
    return user


def get_websocket_subprotocol(scope: dict) -> str | None:
    """
    Return the subprotocol to negotiate during WebSocket accept().
    When XtermConsole or a browser client requests subprotocols (e.g. ['token', '<wsToken>']),
    RFC 6455 requires the server to echo the accepted subprotocol in Sec-WebSocket-Protocol.
    Failing to do so causes the browser to close the connection immediately with code 1006.
    """
    subprotocols = [p for p in (scope.get('subprotocols') or []) if p]
    if not subprotocols:
        return None
    # Always negotiate 'token' rather than echoing a raw or 'token.<key>' secret.
    return 'token'
