"""WebSocket consumers for deployment real-time features."""
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class TerminalConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for terminal access to deployments.

    SECURITY: Requires authentication and ownership verification.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deployment_id = None
        self.user = None

    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']
        self.user = None

        # ======================================================================
        # SECURITY: Authenticate WebSocket connection via token
        # ======================================================================
        query_string = self.scope.get('query_string', b'').decode()
        token_key = None
        for param in query_string.split('&'):
            if param.startswith('token='):
                token_key = param.split('=', 1)[1]
                break

        if not token_key:
            logger.warning(
                "WebSocket connection rejected: No token provided for "
                "deployment %s", self.deployment_id)
            await self.close(code=4001)
            return

        # Validate token
        self.user = await self._authenticate_token(token_key)
        if not self.user:
            logger.warning(
                "WebSocket connection rejected: Invalid token for "
                "deployment %s", self.deployment_id)
            await self.close(code=4002)
            return

        # ======================================================================
        # SECURITY: Verify user owns this deployment
        # ======================================================================
        if not await self._verify_ownership():
            logger.warning(
                "WebSocket connection rejected: User %s doesn't own "
                "deployment %s", self.user.id, self.deployment_id)
            await self.close(code=4003)
            return

        logger.info(
            "WebSocket connected: User %s to deployment %s",
            self.user.id, self.deployment_id)
        await self.accept()
        await self.send(text_data=json.dumps({
            'message': f'Connected to terminal for deployment '
                       f'{self.deployment_id}...\r\n$ '
        }))

    async def disconnect(self, close_code):
        if self.user:
            logger.info(
                "WebSocket disconnected: User %s from "
                "deployment %s", self.user.id, self.deployment_id)

    async def receive(self, text_data):
        # SECURITY: Re-check authentication on each message
        if not self.user:
            await self.close(code=4001)
            return

        # The frontend buffers a full line and sends it on Enter.
        command = (text_data or "").strip()

        if not command:
            response = "\r\n$ "
        elif command == "help":
            response = "\r\nAvailable commands: ls, whoami, help\r\n$ "
        elif command == "ls":
            response = (
                "\r\nbin  boot  dev  etc  home  lib  media  mnt  "
                "opt  proc  root  run  sbin  srv  sys  tmp  usr  var"
                "\r\n$ "
            )
        elif command == "whoami":
            response = f"\r\n{self.user.username}\r\n$ "
        else:
            response = f"\r\nbash: {command}: command not found\r\n$ "

        await self.send(text_data=json.dumps({'message': response}))

    @database_sync_to_async
    def _authenticate_token(self, token_key):
        """Validate token and return user."""
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


class BuildLogConsumer(AsyncWebsocketConsumer):
    """
    Real-time build log streaming consumer.

    Connects to a channel group per deployment and streams build log
    updates as they happen. The Celery task sends logs via channel_layer.

    Usage:
        ws://host/ws/build-logs/{deployment_id}/?token=xxx

    Messages sent to client:
        {
            "type": "build_log",
            "log": "Building image...\n",
            "status": "BUILDING",
            "timestamp": "2026-02-09T17:00:00Z"
        }
        {
            "type": "status_change",
            "status": "ACTIVE",
            "finished_at": "2026-02-09T17:05:00Z",
            "duration_seconds": 300
        }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deployment_id = None
        self.group_name = None
        self.user = None

    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']

        # Authenticate
        query_string = self.scope.get('query_string', b'').decode()
        token_key = None
        for param in query_string.split('&'):
            if param.startswith('token='):
                token_key = param.split('=', 1)[1]
                break

        if not token_key:
            await self.close(code=4001)
            return

        self.user = await self._authenticate_token(token_key)
        if not self.user:
            await self.close(code=4002)
            return

        if not await self._verify_ownership():
            await self.close(code=4003)
            return

        # Join the deployment's log group
        self.group_name = f"build_logs_{self.deployment_id}"
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

        # Send current logs and status as initial payload
        initial = await self._get_current_state()
        await self.send(text_data=json.dumps({
            'type': 'initial_state',
            **initial
        }))

    async def disconnect(self, close_code):
        if self.group_name:
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    # ── Channel layer handlers ──────────────────────────────────────────

    async def build_log(self, event):
        """Handle build log append from Celery task."""
        await self.send(text_data=json.dumps({
            'type': 'build_log',
            'log': event['log'],
            'status': event.get('status', ''),
            'timestamp': event.get('timestamp', ''),
        }))

    async def status_change(self, event):
        """Handle deployment status change."""
        await self.send(text_data=json.dumps({
            'type': 'status_change',
            'status': event['status'],
            'finished_at': event.get('finished_at', ''),
            'duration_seconds': event.get('duration_seconds'),
        }))

    # ── Database helpers ────────────────────────────────────────────────

    @database_sync_to_async
    def _authenticate_token(self, token_key):
        from rest_framework.authtoken.models import Token
        try:
            token = Token.objects.select_related('user').get(key=token_key)
            return token.user
        except Token.DoesNotExist:
            return None

    @database_sync_to_async
    def _verify_ownership(self):
        from apps.deployments.models import Deployment
        try:
            return Deployment.objects.filter(
                id=self.deployment_id,
                service__owner=self.user
            ).exists()
        except Exception:
            return False

    @database_sync_to_async
    def _get_current_state(self):
        """Fetch current build logs and status for initial load."""
        from apps.deployments.models import Deployment
        try:
            d = Deployment.objects.get(id=self.deployment_id)
            return {
                'build_logs': d.build_logs,
                'status': d.status,
                'started_at': d.started_at.isoformat() if d.started_at else None,
                'finished_at': d.finished_at.isoformat() if d.finished_at else None,
                'duration_seconds': d.duration_seconds,
                'commit_hash': d.commit_hash,
                'commit_message': d.commit_message,
            }
        except Deployment.DoesNotExist:
            return {'error': 'Deployment not found'}
