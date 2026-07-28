"""Real-time runtime container log streaming consumer."""
import asyncio
import contextlib
import json
import os
import signal
import subprocess

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .base import authenticate_ws_token, get_websocket_subprotocol, verify_deployment_ownership, logger


class RuntimeLogConsumer(AsyncWebsocketConsumer):
    """
    Real-time runtime container log streaming consumer.

    Streams Docker logs from service containers via WebSocket.
    Uses the same multi-strategy container lookup as the REST endpoint:
      1. deployment.container_id (direct lookup)
      2. smsly.service_id label
      3. service.name substring match

    Usage:
        ws://host/ws/runtime-logs/{deployment_id}/?token=xxx&tail=200

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
            "container_id": "abc123",
            "container_status": "running"
        }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deployment_id = None
        self.group_name = None
        self.user = None
        self._stream_task = None
        self._proc = None
        self._disconnected = False

    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']

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

            await self.accept(subprotocol=get_websocket_subprotocol(self.scope))

            self.group_name = f"runtime_logs_{self.deployment_id}"
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )

            query_string = self.scope.get('query_string', b'').decode()

            tail = 200
            for param in query_string.split('&'):
                if param.startswith('tail='):
                    try:
                        tail = min(int(param.split('=', 1)[1]), 2000)
                    except ValueError:
                        pass

            initial = await self._get_initial_state(tail)
            await self.send(text_data=json.dumps({
                'type': 'initial_state',
                **initial
            }))

            # Start streaming if container is running
            if initial.get('container_status') == 'running':
                self._stream_task = asyncio.create_task(self._stream_logs(tail))

        except Exception as e:
            if settings.DEBUG:
                logger.error("RuntimeLogConsumer.connect() failed: %s", e, exc_info=True)
            with contextlib.suppress(Exception):
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def disconnect(self, _code):
        self._disconnected = True
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_task
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            self._proc = None
        if self.group_name:
            with contextlib.suppress(Exception):
                await self.channel_layer.group_discard(
                    self.group_name,
                    self.channel_name
                )

    async def receive(self, text_data=None, _bytes_data=None):
        if text_data:
            try:
                data = json.loads(text_data)
                if data.get('type') == 'ping':
                    await self.send(text_data=json.dumps({'type': 'pong'}))
                elif data.get('type') == 'refresh':
                    # Client requests a full log refresh
                    state = await self._get_initial_state(
                        data.get('tail', 200)
                    )
                    await self.send(text_data=json.dumps({
                        'type': 'initial_state',
                        **state,
                    }))
            except json.JSONDecodeError:
                pass

    async def log_event(self, event):
        await self.send(text_data=json.dumps({
            'type': 'log',
            'log': event.get('log', ''),
            'timestamp': event.get('timestamp', ''),
        }))

    async def _verify_ownership(self):
        return await verify_deployment_ownership(self.user, self.deployment_id)

    @database_sync_to_async
    def _get_initial_state(self, tail=200):
        from apps.deployments.models import Deployment
        from apps.deployments.views.deployment.logs import _find_container_for_logs
        try:
            dep = Deployment.objects.get(id=self.deployment_id)
            container, source = _find_container_for_logs(dep)

            if not container:
                # Fallback to saved crash logs
                saved_logs = dep.build_logs or ""
                import re as _re
                crash_match = _re.search(
                    r"--- (?:Runtime Crash Logs|Runtime Failure Logs)[^\n]*\n(.*?)--- End (?:Crash|Failure) Logs ---",
                    saved_logs, _re.DOTALL
                )
                fallback = crash_match.group(1).strip() if crash_match else (saved_logs[-4000:] if saved_logs else "")
                return {
                    'logs': fallback,
                    'status': dep.status,
                    'container_id': dep.container_id or '',
                    'container_status': 'stopped',
                    'source': 'build_logs',
                }

            logs = container.logs(
                stdout=True, stderr=True,
                tail=tail, timestamps=True,
            ).decode('utf-8', errors='replace')

            return {
                'logs': logs,
                'status': dep.status,
                'container_id': container.short_id,
                'container_status': container.status,
                'source': 'live_container',
                'lookup': source,
            }
        except Exception as e:
            logger.error("Failed to get initial runtime log state: %s", e)
            return {
                'logs': '', 'status': 'error',
                'container_id': '', 'container_status': 'error',
            }

    async def _stream_logs(self, tail=200):
        """Stream Docker logs from the container using subprocess."""
        try:
            from apps.deployments.models import Deployment
            from apps.deployments.views.deployment.logs import _find_container_for_logs

            dep = await database_sync_to_async(
                Deployment.objects.get
            )(id=self.deployment_id)
            container, source = await database_sync_to_async(
                _find_container_for_logs
            )(dep)

            if not container:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'error': 'Container not found for streaming',
                }))
                return

            container_name = container.name
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': f'Failed to resolve container: {e}',
            }))
            return

        try:
            self._proc = subprocess.Popen(
                ['docker', 'logs', '--tail', str(tail), '-f', '--timestamps', container_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,  # process group for clean terminate
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
            logger.debug("Runtime log stream ended for %s: %s", self.deployment_id, e)
        finally:
            if self._proc:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    pass
                self._proc = None

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)
