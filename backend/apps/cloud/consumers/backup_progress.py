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
        self.api_token = None
        self._accepted = False

    async def connect(self):
        self.backup_id = self.scope['url_route']['kwargs']['backup_id']
        try:
            self.user = self.scope.get('user')
            self.api_token = self.scope.get('api_token')
            if (
                not self.user
                or not getattr(self.user, 'is_authenticated', False)
                or not getattr(self.user, 'is_active', True)
            ):
                await self.close(code=4001)
                return
            if not await self._verify_ownership():
                await self.close(code=4003)
                return
            await self.accept(subprotocol=get_websocket_subprotocol(self.scope))
            self._accepted = True
            self.group_name = f"backup_progress_{self.backup_id}"
            await self.channel_layer.group_add(self.group_name, self.channel_name)
        except Exception as exc:
            if settings.DEBUG:
                logger.error("BackupProgressConsumer.connect() failed: %s", exc, exc_info=True)
            if self._accepted:
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
        from django.utils import timezone
        from apps.deployments.models.backup import ServiceBackup, ServerBackup

        api_token = getattr(self, 'api_token', None)
        if api_token is not None and not api_token.has_scope('read'):
            return False

        try:
            sb = ServiceBackup.objects.filter(id=self.backup_id).select_related(
                'service', 'service__owner', 'service__project__team'
            ).first()
            if sb and sb.service:
                if api_token is not None and not api_token.has_project(sb.service.project_id):
                    return False
                if getattr(self.user, 'is_superuser', False):
                    return True
                if sb.service.owner_id == self.user.id:
                    return True
                team = getattr(getattr(sb.service, 'project', None), 'team', None)
                if team is not None:
                    return team.members.filter(
                        user=self.user,
                        is_active=True,
                    ).exclude(
                        expires_at__isnull=False,
                        expires_at__lt=timezone.now(),
                    ).exists()
                return False

            srv_b = ServerBackup.objects.filter(id=self.backup_id).first()
            if srv_b:
                if api_token is not None and api_token.project_scope() is not None:
                    return False
                if getattr(self.user, 'is_superuser', False):
                    return True
                if getattr(srv_b, 'owner_id', None) == self.user.id or getattr(srv_b, 'created_by_id', None) == self.user.id:
                    return True
                return bool(getattr(self.user, 'is_staff', False))
        except Exception as exc:
            logger.debug("Failed to authorize backup progress for backup %s: %s", self.backup_id, exc)
        return False
