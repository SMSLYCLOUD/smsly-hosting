
import hashlib
from typing import Optional
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.db import close_old_connections

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
    Custom middleware to authenticate users based on a 'token' query parameter.
    Standard for xterm.js WebSockets where Headers cannot be easily sent in browsers.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # 1. Parse query string
        query_string = scope.get('query_string', b'').decode('utf-8')
        query_params = parse_qs(query_string)
        token_key = query_params.get('token', [None])[0]

        # 2. Authenticate if token exists
        if token_key:
            close_old_connections()
            scope['user'] = await get_user_from_token(token_key)
        
        return await self.app(scope, receive, send)
