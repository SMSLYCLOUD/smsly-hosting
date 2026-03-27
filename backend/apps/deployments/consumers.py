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
        self._raw_sock = None  # The actual OS-level socket for recv/send
        self._read_task = None
        self._send_task = None
        self._out_queue = asyncio.Queue()
        self.is_disconnected = False

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
            "[CONSOLE_DEBUG] WS connected: User %s, PID %s, deployment %s",
            self.user.id, os.getpid(), self.deployment_id)
        await self.accept()
        
        # ── START SENDER IMMEDIATELY: Prevent any silent window ──
        self._send_task = asyncio.create_task(self._send_loop())
        
        # ── STATUS UPDATE: Immediate traffic for the proxy ──
        await self._out_queue.put({'message': '\x1b[90m[status] initializing stable tunnel...\x1b[0m\r\n'})

        # Find the container and start docker exec
        self.container_id = await self._find_container()
        if not self.container_id:
            logger.error("Terminal connect: No container found for deployment %s", self.deployment_id)
            await self.send(text_data=json.dumps({
                'message': '\x1b[31m[error] No running container found for '
                           'this deployment.\x1b[0m\r\n'
            }))
            return

        logger.info("Terminal connect: Found container %s for deployment %s", self.container_id, self.deployment_id)
        
        # ── STATUS UPDATE: Keep proxy alive during exec creation ──
        await self.send(text_data=json.dumps({'message': '\x1b[90m[status] attaching to container shell...\x1b[0m\r\n'}))

        # Start the exec session
        success = await self._start_exec()
        if not success:
            logger.error("Terminal connect: Failed to start exec in %s", self.container_id)
            await self.send(text_data=json.dumps({
                'message': '\x1b[31m[error] Failed to start shell in '
                           'container.\x1b[0m\r\n'
            }))
            return

        logger.info("Terminal connect: Shell started in %s", self.container_id)

        # ── PRIME THE PIPE: Send a full banner to keep proxies alive ──
        banner = (
            "\x1b[32m[connected to container]\x1b[0m\r\n"
            "\x1b[90m--------------------------------------------------\x1b[0m\r\n"
            f"\x1b[90mDeployment ID: {self.deployment_id}\x1b[0m\r\n"
            f"\x1b[90mContainer ID:  {self.container_id[:12]}\x1b[0m\r\n"
            "\x1b[90m--------------------------------------------------\x1b[0m\r\n\r\n"
        )
        await self.send(text_data=json.dumps({'message': banner}))

        # Start background tasks
        # Decouple reading (blocking) from sending (async) via a queue
        self._read_task = asyncio.create_task(self._read_output())
        self._send_task = asyncio.create_task(self._send_loop())

    async def disconnect(self, close_code):
        self.is_disconnected = True
        if self._read_task:
            self._read_task.cancel()
        if self._send_task:
            self._send_task.cancel()
        
        try:
            if self._read_task:
                await asyncio.wait_for(self._read_task, timeout=0.5)
            if self._send_task:
                await asyncio.wait_for(self._send_task, timeout=0.5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        self._close_exec_socket()
        if self.user:
            logger.info(
                "WebSocket disconnected: User %s from "
                "deployment %s", self.user.id, self.deployment_id)

    def _close_exec_socket(self):
        """Safely close the exec socket and raw socket."""
        for sock in (self._raw_sock, self.exec_socket):
            if sock is None:
                continue
            try:
                sock.close()
            except Exception:
                pass
        self._raw_sock = None
        self.exec_socket = None
        self.exec_id = None

    async def receive(self, text_data):
        # SECURITY: Re-check authentication on each message
        if not self.user:
            await self.close(code=4001)
            return

        # Handle structured messages (e.g. heartbeat)
        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                # Respond with pong to keep connection alive through proxies
                await self.send(text_data=json.dumps({'type': 'pong'}))
                return
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        raw = self._raw_sock or self.exec_socket
        if not raw:
            return

        # Update activity timestamp on input
        import time
        self._last_activity = time.time()

        # Forward raw input to the container's exec stdin
        try:
            if hasattr(raw, 'send'):
                await asyncio.get_event_loop().run_in_executor(
                    None, raw.send, text_data.encode('utf-8'))
            elif hasattr(raw, 'write'):
                await asyncio.get_event_loop().run_in_executor(
                    None, raw.write, text_data.encode('utf-8'))
        except Exception as e:
            logger.error("Terminal exec send error for %s: %s", self.deployment_id, e)
            try:
                await self.send(text_data=json.dumps({
                    'message': f'\r\n\x1b[31m[send-error] {str(e)}\x1b[0m\r\n'
                }))
            except Exception:
                pass

    async def _read_output(self):
        """Background task: read from exec socket and send to WebSocket.

        If the exec socket dies, attempts server-side reconnection up to
        3 times before giving up and closing the WebSocket.
        """
        loop = asyncio.get_event_loop()
        import time

        if not hasattr(self, '_last_activity'):
            self._last_activity = time.time()

        timeout_seconds = 420.0  # 7 minutes total idle timeout
        max_exec_reconnects = 3
        exec_reconnect_count = 0

        try:
            while True:
                data = await loop.run_in_executor(None, self._blocking_read)

                if data is None:
                    # Exec socket died — try server-side reconnection
                    exec_reconnect_count += 1
                    if exec_reconnect_count > max_exec_reconnects:
                        logger.info(
                            "Terminal exec reconnect limit reached for %s",
                            self.deployment_id)
                        try:
                            await self._out_queue.put({
                                'message': '\r\n\x1b[31m[session ended — '
                                           'exec reconnect limit reached]\x1b[0m\r\n'
                            })
                            # Wait slightly for the queue to drain
                            await asyncio.sleep(0.5)
                            await self.close(code=4000)
                        except Exception:
                            pass
                        break

                    logger.info(
                        "Terminal exec socket died for %s, reconnecting "
                        "(%d/%d)", self.deployment_id,
                        exec_reconnect_count, max_exec_reconnects)
                    try:
                        await self._out_queue.put({
                            'message': f'\r\n\x1b[33m[exec disconnected — '
                                       f'reconnecting {exec_reconnect_count}/'
                                       f'{max_exec_reconnects}]\x1b[0m\r\n'
                        })
                    except Exception:
                        pass

                    # Clean up old socket and try again
                    self._close_exec_socket()
                    await asyncio.sleep(1.0)

                    # Re-find container (may have restarted)
                    self.container_id = await self._find_container()
                    if not self.container_id:
                        continue
                    success = await self._start_exec()
                    if not success:
                        continue

                    try:
                        await self._out_queue.put({
                            'message': '\x1b[32m[reconnected to container]'
                                       '\x1b[0m\r\n'
                        })
                    except Exception:
                        pass
                    continue

                if data == b'':
                    # Socket timeout (not EOF) — check idle timeout, send keepalive
                    if time.time() - self._last_activity > timeout_seconds:
                        logger.info(
                            "Terminal idle timeout for %s",
                            self.deployment_id)
                        try:
                            await self.send(text_data=json.dumps({
                                'type': 'error',
                                'message': '\r\n\x1b[31m[idle timeout]\x1b[0m\r\n'
                            }))
                            await self.close(code=4000)
                        except Exception:
                            pass
                        break

                    # Send a structured keepalive to prevent proxy idle timeout
                    try:
                        await self._out_queue.put({'message': ''})
                    except Exception:
                        pass
                    continue

                # Got real data — reset activity and exec reconnect counters
                if self.is_disconnected:
                    break

                self._last_activity = time.time()
                exec_reconnect_count = 0
                text = data.decode('utf-8', errors='replace')
                await self._out_queue.put({'message': text})
        except asyncio.CancelledError:
            logger.info("[CONSOLE_DEBUG] _read_output task CANCELLED")
        except Exception as e:
            if not self.is_disconnected:
                logger.error(
                    "[CONSOLE_DEBUG] _read_output error (PID %s): %s",
                    os.getpid(), e, exc_info=True)
            try:
                await self.close()
            except Exception:
                pass
        finally:
            logger.info("[CONSOLE_DEBUG] _read_output task TERMINATED")

    async def _send_loop(self):
        """Dedicated task to drain the output queue to the WebSocket with heartbeat."""
        try:
            while not self.is_disconnected:
                try:
                    # Wait for message with a 10s pulse timeout
                    msg = await asyncio.wait_for(self._out_queue.get(), timeout=10.0)
                    try:
                        await self.send(text_data=json.dumps(msg))
                    except Exception as e:
                        logger.warning("Terminal WebSocket send failed: %s", e)
                        break
                except asyncio.TimeoutError:
                    # No data for 10s — send a server-side pulse to keep proxies awake
                    if not self.is_disconnected:
                        try:
                            await self.send(text_data=json.dumps({'type': 'pulse'}))
                        except Exception:
                            # If pulse fails, the socket is dead
                            break
                except asyncio.QueueEmpty:
                    # ── HIGH-FREQ PULSE: Keep proxy alive during long attachment ──
                    # Use 1s pulse until shell is ready, then 10s
                    pulse_delay = 1.0 if not self.exec_id else 10.0
                    await asyncio.sleep(pulse_delay)
                    try:
                        await self.send(text_data=json.dumps({'type': 'pulse'}))
                    except Exception:
                        # If pulse fails, the socket is dead
                        break
        except asyncio.CancelledError:
            logger.info("[CONSOLE_DEBUG] _send_loop task CANCELLED")
        except Exception as e:
            logger.error("[CONSOLE_DEBUG] _send_loop error (PID %s): %s", os.getpid(), e)
        finally:
            logger.info("[CONSOLE_DEBUG] _send_loop task TERMINATED")

    def _blocking_read(self):
        """Blocking read from the exec socket. Runs in executor.

        Uses select.select to implement a reliable timeout that works
        on both raw sockets and docker-py wrappers.
        """
        import select
        import socket

        sock = self._raw_sock or self.exec_socket
        if sock is None:
            return None

        try:
            if hasattr(sock, 'settimeout'):
                sock.settimeout(15.0)

            if hasattr(sock, 'recv'):
                data = sock.recv(4096)
            elif hasattr(sock, 'read'):
                data = sock.read(4096)
            else:
                logger.error("Exec socket has no read or recv method")
                return None

            if not data:
                # True EOF — connection closed
                return None
            return data

        except socket.timeout:
            return b''
        except select.error:
            return b''
        except Exception as e:
            err_name = e.__class__.__name__
            err_str = str(e).lower()
            # urllib3/requests timeout variants
            if err_name == 'ReadTimeoutError' or 'timed out' in err_str or 'timeout' in err_str:
                return b''
            if 'connection broken' in err_str or 'chunkedencodingerror' in err_name.lower():
                return b''
            logger.error(
                "Terminal _blocking_read exception for %s: %s - %s",
                self.deployment_id, type(e), e)
            return None

    @database_sync_to_async
    def _find_container(self):
        """Find the Docker container ID for this deployment's service."""
        from apps.cloud.docker_client import get_docker_exec_client
        from apps.deployments.models import Deployment
        try:
            dep = Deployment.objects.select_related('service').get(
                id=self.deployment_id)
            service_name = dep.service.name

            client = get_docker_exec_client()
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
        """Create a docker exec instance and attach to it.

        Extracts the raw OS socket from docker-py's wrapper so that
        _blocking_read() can use recv() for reliable timeout behavior.
        """
        from apps.cloud.docker_client import get_docker_exec_client
        import socket as _socket
        try:
            client = get_docker_exec_client()
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
            logger.info("Exec instance created: %s (shell: %s)", self.exec_id, shell)

            # Attach to the exec instance (returns a socket-like wrapper)
            self.exec_socket = client.api.exec_start(
                self.exec_id,
                socket=True,
                tty=True,
            )
            logger.info("Exec session attached for %s", self.exec_id)

            # ── CRITICAL: Extract the raw OS socket ──
            # docker-py returns a SocketIO/HTTPResponse wrapper whose read()
            # method returns b'' on BOTH timeout AND EOF, making them
            # indistinguishable. The raw socket's recv() properly raises
            # socket.timeout, so we always use that.
            raw = getattr(self.exec_socket, '_sock', None)
            if raw is None:
                # Some docker-py versions expose the socket differently
                raw = self.exec_socket
            self._raw_sock = raw

            # Set recv timeout so _blocking_read cycles every 15s
            # instead of blocking forever
            try:
                self._raw_sock.settimeout(15.0)
            except (AttributeError, _socket.error) as e:
                logger.warning("Could not set exec socket timeout: %s", e)

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
