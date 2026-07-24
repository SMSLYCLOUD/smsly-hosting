"""Real-time build log streaming consumer."""
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .base import authenticate_ws_token, get_websocket_subprotocol, logger


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

        await self.accept(subprotocol=get_websocket_subprotocol(self.scope))

        try:
            self.user = self.scope.get('user')

            if not self.user or not getattr(self.user, 'is_authenticated', False):
                await self.send(text_data=json.dumps({'error': 'Missing or invalid token'}))
                await self.close(code=4001)
                return

            if not await self._verify_ownership():
                await self.send(text_data=json.dumps({'error': 'Access denied'}))
                await self.close(code=4003)
                return

            self.group_name = f"build_logs_{self.deployment_id}"
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )

            initial = await self._get_current_state()
            await self.send(text_data=json.dumps({
                'type': 'initial_state',
                **initial
            }))
        except Exception as e:
            if settings.DEBUG:
                logger.error("BuildLogConsumer.connect() failed: %s", e, exc_info=True)
            with contextlib.suppress(Exception):
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def disconnect(self, code):
        if self.group_name:
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def receive(self, text_data=None, bytes_data=None):
        if not await self._revalidate_auth():
            await self.close(code=4001)
            return

    async def _revalidate_auth(self) -> bool:
        if not self.user or not self.deployment_id:
            return False
        return await self._verify_ownership()

    async def build_log(self, event):
        await self.send(text_data=json.dumps({
            'type': 'build_log',
            'log': event['log'],
            'status': event.get('status', ''),
            'timestamp': event.get('timestamp', ''),
        }))

    async def status_change(self, event):
        await self.send(text_data=json.dumps({
            'type': 'status_change',
            'status': event['status'],
            'finished_at': event.get('finished_at', ''),
            'duration_seconds': event.get('duration_seconds'),
        }))

    async def pipeline_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'pipeline_update',
            'stages': event.get('stages', []),
        }))

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)

    @database_sync_to_async
    def _verify_ownership(self):
        from django.db.models import Q
        from apps.deployments.models import Deployment
        try:
            return Deployment.objects.filter(
                Q(service__owner=self.user) |
                Q(service__project__team__members__user=self.user),
                id=self.deployment_id,
            ).exists()
        except Exception:
            return False

    @database_sync_to_async
    def _get_current_state(self):
        from apps.deployments.models import Deployment
        try:
            d = Deployment.objects.get(id=self.deployment_id)
            safe_logs = (d.build_logs or "").replace('\x00', '')
            return {
                'build_logs': safe_logs,
                'status': d.status,
                'started_at': d.started_at.isoformat() if d.started_at else None,
                'finished_at': d.finished_at.isoformat() if d.finished_at else None,
                'duration_seconds': d.duration_seconds,
                'commit_hash': d.commit_hash,
                'commit_message': d.commit_message,
            }
        except Deployment.DoesNotExist:
            return {'error': 'Deployment not found'}
