"""Real-time addon container log streaming consumer."""
import asyncio
import contextlib
import json
import logging
import subprocess

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .base import authenticate_ws_token, get_websocket_subprotocol, logger


class AddonLogConsumer(AsyncWebsocketConsumer):
    """
    Real-time addon container log streaming consumer.

    Streams Docker logs from addon containers via WebSocket.
    Supports both provisioned addons (managed by the platform) and
    self-contained addons (user-deployed containers).

    Usage:
        ws://host/ws/addon-logs/{addon_id}/?token=xxx&tail=200

    Messages sent to client:
        {
            "type": "log",
            "log": "2026-07-06T03:21:47 ...",
            "timestamp": "2026-07-06T03:21:47Z"
        }
        {
            "type": "initial_state",
            "logs": "...",
            "status": "ACTIVE",
            "addon_type": "POSTGRES"
        }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.addon_id = None
        self.group_name = None
        self.user = None
        self._stream_task = None
        self._proc = None
        self._disconnected = False

    async def connect(self):
        self.addon_id = self.scope['url_route']['kwargs']['addon_id']

        await self.accept(subprotocol=get_websocket_subprotocol(self.scope))

        try:
            self.user = self.scope.get('user')

            if not self.user or self.user.is_anonymous:
                await self.send(text_data=json.dumps({'error': 'Authentication required'}))
                await self.close(code=4002)
                return

            if not await self._verify_ownership():
                await self.send(text_data=json.dumps({'error': 'Access denied'}))
                await self.close(code=4003)
                return

            self.group_name = f"addon_logs_{self.addon_id}"
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )

            query_string = self.scope.get('query_string', b'').decode()

            initial = await self._get_initial_state()
            await self.send(text_data=json.dumps({
                'type': 'initial_state',
                **initial
            }))

            tail = 200
            for param in query_string.split('&'):
                if param.startswith('tail='):
                    try:
                        tail = min(int(param.split('=', 1)[1]), 2000)
                    except ValueError:
                        pass
            self._stream_task = asyncio.create_task(self._stream_logs(tail))

        except Exception as e:
            if settings.DEBUG:
                logger.error("AddonLogConsumer.connect() failed: %s", e, exc_info=True)
            with contextlib.suppress(Exception):
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def disconnect(self, code):
        self._disconnected = True
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_task
        if self._proc:
            with contextlib.suppress(Exception):
                self._proc.terminate()
            self._proc = None
        if self.group_name:
            with contextlib.suppress(Exception):
                await self.channel_layer.group_discard(
                    self.group_name,
                    self.channel_name
                )

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            try:
                data = json.loads(text_data)
                if data.get('type') == 'ping':
                    await self.send(text_data=json.dumps({'type': 'pong'}))
            except json.JSONDecodeError:
                pass

    async def log_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'log',
            'log': event.get('log', ''),
            'timestamp': event.get('timestamp', ''),
        }))

    @database_sync_to_async
    def _verify_ownership(self):
        from django.db.models import Q
        from .models.addons import Addon
        try:
            addon = Addon.objects.select_related('service', 'service__owner').get(id=self.addon_id)
            return addon.service.owner_id == self.user.id or addon.service.project.team.members.filter(user=self.user).exists()
        except Addon.DoesNotExist:
            return False

    @database_sync_to_async
    def _get_initial_state(self):
        from .models.addons import Addon
        try:
            addon = Addon.objects.get(id=self.addon_id)
            container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
            from apps.addons.services.addon_provisioner import addon_provisioner
            logs = addon_provisioner.get_logs(container_name, tail=200)
            return {
                'logs': logs,
                'status': addon.status,
                'addon_type': addon.addon_type,
                'container_name': container_name,
            }
        except Addon.DoesNotExist:
            return {'logs': '', 'status': 'unknown', 'addon_type': '', 'container_name': ''}
        except Exception as e:
            logger.error("Failed to get initial addon log state: %s", e)
            return {'logs': '', 'status': 'error', 'addon_type': '', 'container_name': ''}

    async def _stream_logs(self, tail=200):
        try:
            from .models.addons import Addon
            addon = await database_sync_to_async(
                Addon.objects.get
            )(id=self.addon_id)
            container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        except Exception:
            await self.send(text_data=json.dumps({'error': 'Addon not found'}))
            return

        try:
            self._proc = subprocess.Popen(
                ['docker', 'logs', '--tail', str(tail), '-f', '--timestamps', container_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            loop = asyncio.get_event_loop()
            while not self._proc.stdout.closed:
                line = await loop.run_in_executor(None, self._proc.stdout.readline)
                if not line:
                    break
                if self._disconnected:
                    break
                await self.send(text_data=json.dumps({
                    'type': 'log',
                    'log': line,
                    'timestamp': '',
                }))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("Addon log stream ended for %s: %s", self.addon_id, e)
        finally:
            if self._proc:
                with contextlib.suppress(Exception):
                    self._proc.terminate()
                self._proc = None

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)
