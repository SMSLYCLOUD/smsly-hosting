"""Real-time backup/restore progress streaming consumer."""
import contextlib
import json
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from apps.deployments.consumers.base import authenticate_ws_token, get_websocket_subprotocol, logger


class BackupProgressConsumer(AsyncWebsocketConsumer):
    """Real-time backup/restore progress streaming consumer.

    Channel group: ``backup_progress_{backup_id}``
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.backup_id = None
        self.group_name = None
        self.user = None

    async def connect(self):
        self.backup_id = self.scope['url_route']['kwargs']['backup_id']
        await self.accept(subprotocol=get_websocket_subprotocol(self.scope))
        try:
            self.user = self.scope.get('user')
            if not self.user or not getattr(self.user, 'is_authenticated', False):
                await self.send(text_data=json.dumps({'error': 'Authentication required'}))
                await self.close(code=4001)
                return
            if not await self._verify_ownership():
                await self.send(text_data=json.dumps({'error': 'Access denied'}))
                await self.close(code=4003)
                return
            self.group_name = f"backup_progress_{self.backup_id}"
            await self.channel_layer.group_add(self.group_name, self.channel_name)
        except Exception:
            if settings.DEBUG:
                logger.error("BackupProgressConsumer.connect() failed: %s", exc_info=True)
            with contextlib.suppress(Exception):
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def disconnect(self, code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        pass

    async def backup_progress(self, event):
        await self.send(text_data=json.dumps({
            'type': 'backup_progress',
            'stage': event['stage'],
            'percent': event.get('percent', 0),
            'message': event.get('message', ''),
            'bytes_transferred': event.get('bytes_transferred'),
            'total_bytes': event.get('total_bytes'),
            'timestamp': event.get('timestamp', ''),
        }))

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)

    @database_sync_to_async
    def _verify_ownership(self):
        from .models.backup import ServiceBackup, ServerBackup
        try:
            sb = ServiceBackup.objects.filter(id=self.backup_id).select_related(
                'service', 'service__owner', 'service__project__team'
            ).first()
            if sb:
                return (
                    sb.service.owner_id == self.user.id
                    or sb.service.project.team.members.filter(user=self.user).exists()
                )
        except Exception as exc:
            logger.debug("Failed to authorize backup progress for service backup %s: %s", self.backup_id, exc)
        try:
            server_bu = ServerBackup.objects.filter(id=self.backup_id).first()
            if server_bu:
                return True
        except Exception as exc:
            logger.debug("Failed to authorize backup progress for server backup %s: %s", self.backup_id, exc)
        return False
