"""WebSocket consumers for deployment real-time features."""
import json
import asyncio
import logging
import os
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class TerminalConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for interactive terminal access to containers.

    SECURITY: Requires authentication and ownership verification.
    Connects to the running Docker container via `docker exec`.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deployment_id = None
        self.user = None
        self.container_id = None
        self.exec_id = None
        self.exec_socket = None
        self._read_task = None

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

        # ======================================================================
        # Find the container and start docker exec
        # ======================================================================
        self.container_id = await self._find_container()
        if not self.container_id:
            await self.send(text_data=json.dumps({
                'message': '\x1b[31m[error] No running container found for '
                           'this deployment.\x1b[0m\r\n'
            }))
            return

        # Start the exec session
        success = await self._start_exec()
        if not success:
            await self.send(text_data=json.dumps({
                'message': '\x1b[31m[error] Failed to start shell in '
                           'container.\x1b[0m\r\n'
            }))
            return

        await self.send(text_data=json.dumps({
            'message': '\x1b[32m[connected to container]\x1b[0m\r\n'
        }))

        # Start background task to read container output
        # Sleep slightly before starting the blocking read so the WS can settle
        await asyncio.sleep(0.1)
        self._read_task = asyncio.create_task(self._read_output())

    async def disconnect(self, close_code):
        if self._read_task:
            self._read_task.cancel()
            try:
                # Give it a moment to cancel gracefully
                await asyncio.wait_for(self._read_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if self.exec_socket:
            try:
                # Use a short timeout so we don't hang the worker
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, self.exec_socket.close),
                    timeout=2.0
                )
            except Exception:
                pass
        if self.user:
            logger.info(
                "WebSocket disconnected: User %s from "
                "deployment %s", self.user.id, self.deployment_id)

    async def receive(self, text_data):
        # SECURITY: Re-check authentication on each message
        if not self.user:
            await self.close(code=4001)
            return

        if not self.exec_socket:
            return

        # Forward raw input to the container's exec stdin
        try:
            # We use character-by-character forwarding for true interactive terminal support.
            # No manual newline appending here — let the client send what it needs.
            # Docker socket wrapper lacks .send(), use underlying _sock.send()
            await asyncio.get_event_loop().run_in_executor(
                None, self.exec_socket._sock.send, text_data.encode('utf-8'))
        except Exception as e:
            logger.error("Terminal exec send error for %s: %s", self.deployment_id, e)
            try:
                await self.send(text_data=json.dumps({
                    'message': f'\r\n\x1b[31m[send-error] {str(e)}\x1b[0m\r\n'
                }))
            except Exception:
                pass

    async def _read_output(self):
        """Background task: read from exec socket and send to WebSocket."""
        loop = asyncio.get_event_loop()
        try:
            while True:
                data = await loop.run_in_executor(None, self._blocking_read)

                if data is None:
                    # None means a real error or disconnect occurred
                    logger.info("Terminal session ended for deployment %s", self.deployment_id)
                    try:
                        await self.send(text_data=json.dumps({
                            'message': '\r\n\x1b[31m[session ended]\x1b[0m\r\n'
                        }))
                    except Exception:
                        pass
                    break

                if data == b'':
                    # If we got exactly b'' from a socket timeout, just sleep briefly and retry
                    # to prevent busy looping but keep connection alive.
                    # Send a ping-like keepalive message to prevent the proxy from dropping the idle WS
                    try:
                        await self.send(text_data=json.dumps({'message': ''}))
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                    continue

                text = data.decode('utf-8', errors='replace')
                await self.send(text_data=json.dumps({'message': text}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Terminal output read error for %s: %s", self.deployment_id, e)

    def _blocking_read(self):
        """Blocking read from the exec socket. Runs in executor."""
        import socket
        import urllib3.exceptions
        import requests.exceptions
        try:
            # The docker-py exec_start(socket=True) returns a SocketIO object.
            # Use .read() instead of .recv() to avoid AttributeError.
            #
            # IMPORTANT: For urllib3 streams, read(4096) might block indefinitely if no data is sent.
            # We must use a smaller amount or rely on the underlying socket timeout.
            data = self.exec_socket.read(4096)

            if not data:
                # Actual EOF (connection closed by remote docker side)
                return None
            return data
        except (socket.timeout, urllib3.exceptions.ReadTimeoutError, requests.exceptions.ReadTimeout, TimeoutError):
            return b''  # Signal that it was just a timeout, not a disconnect
        # Catch ChunkedEncodingError which happens when the connection is prematurely closed
        except requests.exceptions.ChunkedEncodingError:
            return None
        except Exception as e:
            # Check if this exception is functionally a timeout disguised as a generic error
            if 'timed out' in str(e).lower() or 'timeout' in str(e).lower():
                return b''
            # 'Connection broken' might happen if the proxy kills it
            if 'connection broken' in str(e).lower():
                return None
            logger.error("Terminal _blocking_read exception (disconnecting): %s - %s", type(e), e)
            return None

    @database_sync_to_async
    def _find_container(self):
        """Find the Docker container ID for this deployment's service."""
        from apps.cloud.docker_client import get_docker_client
        from apps.deployments.models import Deployment
        try:
            dep = Deployment.objects.select_related('service').get(
                id=self.deployment_id)
            service_name = dep.service.name

            client = get_docker_client()
            # Look for a running container matching the service name
            containers = client.containers.list(
                filters={'name': service_name, 'status': 'running'})
            if containers:
                return containers[0].id

            # Also try by label
            containers = client.containers.list(
                filters={'label': f'smsly.service={service_name}',
                         'status': 'running'})
            if containers:
                return containers[0].id

            return None
        except Exception as e:
            logger.error("Error finding container: %s", e)
            return None

    @database_sync_to_async
    def _start_exec(self):
        """Create a docker exec instance and attach to it."""
        from apps.cloud.docker_client import get_docker_client
        try:
            client = get_docker_client()
            container = client.containers.get(self.container_id)

            # Try bash first, fall back to sh
            shell = '/bin/bash'
            try:
                exit_code, _ = container.exec_run(
                    'which bash', demux=True)
                if exit_code != 0:
                    shell = '/bin/sh'
            except Exception:
                shell = '/bin/sh'

            # Create exec instance with TTY
            exec_instance = client.api.exec_create(
                self.container_id,
                shell,
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
            )
            self.exec_id = exec_instance['Id']

            # Attach to the exec instance (returns a socket-like object)
            self.exec_socket = client.api.exec_start(
                self.exec_id,
                socket=True,
                tty=True,
            )

            # Prevent the socket from aggressively timing out during idle periods
            # The docker-py client uses a wrapped socket.
            try:
                # The underlying python socket
                if hasattr(self.exec_socket, '_sock'):
                    self.exec_socket._sock.settimeout(60.0)
            except Exception as e:
                logger.debug("Could not set timeout on exec_socket: %s", e)

            return True
        except Exception as e:
            logger.error("Error starting exec: %s", e)
            return False

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

    async def pipeline_update(self, event):
        """Handle pipeline stages update from Celery task."""
        await self.send(text_data=json.dumps({
            'type': 'pipeline_update',
            'stages': event.get('stages', []),
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
