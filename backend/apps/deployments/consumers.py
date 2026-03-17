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
        self._read_task = asyncio.ensure_future(self._read_output())

    async def disconnect(self, close_code):
        if self._read_task:
            self._read_task.cancel()
        if self.exec_socket:
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.exec_socket.close)
            except Exception:
                pass
        if self.user:
            logger.info(
                "WebSocket disconnected: User %s from "
                "deployment %s", self.user.id, self.deployment_id)

        # Differentiate between control messages (JSON) and raw input (string)
        try:
            data = json.loads(text_data)
            if isinstance(data, dict):
                if data.get('type') == 'resize':
                    cols = data.get('cols')
                    rows = data.get('rows')
                    if cols and rows:
                        await self._resize_tty(cols, rows)
                    return
        except (json.JSONDecodeError, TypeError):
            # Not a control message, treat as raw input
            pass

        # Forward raw input to the container's exec stdin
        try:
            if self.exec_socket:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.exec_socket.send, text_data.encode('utf-8'))
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
                if not data:
                    logger.info("Terminal session ended for deployment %s", self.deployment_id)
                    try:
                        await self.send(text_data=json.dumps({
                            'message': '\r\n\x1b[31m[session ended]\x1b[0m\r\n'
                        }))
                    except Exception:
                        pass
                    break

                text = data.decode('utf-8', errors='replace')
                await self.send(text_data=json.dumps({'message': text}))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Terminal output read error for %s: %s", self.deployment_id, e)

    def _blocking_read(self):
        """Blocking read from the exec socket. Runs in executor."""
        try:
            if not self.exec_socket:
                return None
                
            # Try various ways to read from the socket (depends on docker-py version)
            if hasattr(self.exec_socket, 'recv'):
                data = self.exec_socket.recv(4096)
            elif hasattr(self.exec_socket, '_sock') and hasattr(self.exec_socket._sock, 'recv'):
                data = self.exec_socket._sock.recv(4096)
            else:
                logger.error("Terminal socket object has no recv method")
                return None
                
            if not data:
                logger.debug("Terminal socket returned EOF for %s", self.deployment_id)
            return data
        except Exception as e:
            logger.error("Terminal socket recv error for %s: %s", self.deployment_id, e)
            return None

    async def _resize_tty(self, cols, rows):
        """Resize the TTY for the exec session."""
        if not self.exec_id:
            return
            
        def _blocking_resize():
            from apps.cloud.docker_client import get_docker_client
            try:
                client = get_docker_client()
                client.api.exec_resize(self.exec_id, height=rows, width=cols)
                return True
            except Exception as e:
                logger.error("Failed to resize terminal for %s: %s", self.deployment_id, e)
                return False
                
        await asyncio.get_event_loop().run_in_executor(None, _blocking_resize)

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
                logger.debug("Found container by name for service %s: %s", service_name, containers[0].id)
                return containers[0].id

            # Also try by label
            containers = client.containers.list(
                filters={'label': f'smsly.service={service_name}',
                         'status': 'running'})
            if containers:
                logger.debug("Found container by label for service %s: %s", service_name, containers[0].id)
                return containers[0].id

            logger.warning("No running container found for service %s", service_name)
            return None
        except Exception as e:
            logger.error("Error finding container for deployment %s: %s", self.deployment_id, e)
            return None

    @database_sync_to_async
    def _start_exec(self):
        """Create a docker exec instance and attach to it."""
        from apps.cloud.docker_client import get_docker_client
        try:
            client = get_docker_client()
            container = client.containers.get(self.container_id)

            # Detect available shell
            shells = ['/bin/bash', '/bin/sh', '/bin/ash', '/bin/zsh', 'sh', 'bash']
            shell = '/bin/sh'
            
            for s in shells:
                try:
                    res = container.exec_run(['which', s])
                    if res.exit_code == 0:
                        shell = s
                        break
                except Exception:
                    continue

            # Create exec instance with TTY
            exec_instance = client.api.exec_create(
                self.container_id,
                [shell],
                stdin=True,
                stdout=True,
                stderr=True,
                tty=True,
                environment={
                    "TERM": "xterm-256color",
                    "LANG": "C.UTF-8",
                    "COLUMNS": "120",
                    "LINES": "40",
                }
            )
            self.exec_id = exec_instance['Id']

            # Attach to the exec instance (returns a socket-like object)
            logger.debug("Starting exec attachment for %s (shell: %s)", self.deployment_id, shell)
            self.exec_socket = client.api.exec_start(
                self.exec_id,
                detach=False,
                tty=True,
                stream=False,
                socket=True,
            )
            
            if hasattr(self.exec_socket, '_socket'):
                # Some versions of docker-py wrap the socket
                self.exec_socket._socket.settimeout(None)
            
            logger.info("Exec socket attached and ready for %s", self.deployment_id)

            return True
        except Exception as e:
            logger.error("Error starting exec for %s: %s", self.deployment_id, e)
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
