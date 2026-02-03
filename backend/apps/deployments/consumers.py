"""Consumers module."""
import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class TerminalConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for terminal access to deployments.

    SECURITY: Requires authentication and ownership verification.
    """

    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']
        self.user = None

        # ==========================================================================
        # SECURITY: Authenticate WebSocket connection via token
        # ==========================================================================
        # Get token from query string (e.g., ws://...?token=xxx)
        query_string = self.scope.get('query_string', b'').decode()
        token_key = None
        for param in query_string.split('&'):
            if param.startswith('token='):
                token_key = param.split('=', 1)[1]
                break

        if not token_key:
            logger.warning(
                f"WebSocket connection rejected: No token provided for deployment {self.deployment_id}")
            await self.close(code=4001)
            return

        # Validate token
        self.user = await self._authenticate_token(token_key)
        if not self.user:
            logger.warning(
                f"WebSocket connection rejected: Invalid token for deployment {self.deployment_id}")
            await self.close(code=4002)
            return

        # ==========================================================================
        # SECURITY: Verify user owns this deployment
        # ==========================================================================
        if not await self._verify_ownership():
            logger.warning(
                f"WebSocket connection rejected: User {self.user.id} doesn't own deployment {self.deployment_id}")
            await self.close(code=4003)
            return

        logger.info(
            f"WebSocket connected: User {self.user.id} to deployment {self.deployment_id}")
        await self.accept()
        await self.send(text_data=json.dumps({
            'message': f'Connected to terminal for deployment {self.deployment_id}...\r\n$ '
        }))

    async def disconnect(self, close_code):
        if self.user:
            logger.info(
                f"WebSocket disconnected: User {self.user.id} from deployment {self.deployment_id}")

    async def receive(self, text_data):
        # SECURITY: Re-check authentication on each message
        if not self.user:
            await self.close(code=4001)
            return

        command = text_data

        if command == '\r':
            response = '\r\n$ '
        else:
            # Echo back
            response = command

            # Simple simulation
            if command.strip() == 'ls':
                response = '\r\nbin  boot  dev  etc  home  lib  media  mnt  opt  proc  root  run  sbin  srv  sys  tmp  usr  var\r\n$ '
            elif command.strip() == 'whoami':
                response = f'\r\n{self.user.username}\r\n$ '

        await self.send(text_data=json.dumps({'message': response}))

    @database_sync_to_async
    def _authenticate_token(self, token_key):
        """Validate token and return user."""
        # Import inside method to avoid AppRegistryNotReady error
        from rest_framework.authtoken.models import Token
        try:
            token = Token.objects.select_related('user').get(key=token_key)
            return token.user
        except Token.DoesNotExist:
            return None

    @database_sync_to_async
    def _verify_ownership(self):
        """Verify user owns the deployment."""
        from apps.deployments.models import Deployment
        try:
            return Deployment.objects.filter(
                id=self.deployment_id,
                service__owner=self.user
            ).exists()
        except Exception:
            return False
