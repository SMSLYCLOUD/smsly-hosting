"""WebSocket consumers for deployment real-time features."""
import json
import asyncio
import logging
import os
import base64
import time
from django.conf import settings
from apps.deployments.utils import log_event
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class TerminalConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for interactive terminal access to containers.

    SECURITY: Requires authentication and ownership verification.
    Connects to the running Docker container via `docker exec`.

    Authentication protocol
    -----------------------
    The DRF auth token is read from a Sec-WebSocket-Protocol subprotocol,
    NEVER from the URL query string. Query strings are captured in
    reverse-proxy access logs (Caddy/Nginx), browser history, and the
    ``Referer`` header of cross-origin requests, and a long-lived DRF
    token must never appear in a URL.

    Client offers one of:
        - ``["token", "<key>"]``  — recommended (the literal ``token`` is
          the protocol marker, the second value is the DRF auth key)
        - ``["token.<key>"]``     — alternate: the key is prefixed with
          the literal ``token.``
        - ``["<key>"]``           — legacy: the single subprotocol is
          the token value itself

    Server accepts with ``Sec-WebSocket-Protocol: token`` so the actual
    auth key is never echoed in the WS handshake response.
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
        self._setup_task = None
        self._cmd_buffer = "" # Buffer for audit logging
        self._out_queue = asyncio.Queue()
        self.is_disconnected = False
        self._accepted = False
        self._last_activity = time.time()
        self._keepalive_timeout_seconds = self._resolve_keepalive_timeout()

    def _resolve_keepalive_timeout(self) -> float:
        """Resolve WS keepalive interval from env with safe bounds."""
        raw_value = os.getenv("TERMINAL_WS_KEEPALIVE_SECONDS", "20")
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            parsed = 20.0
        # Keep the value practical for proxy idle limits and avoid noisy churn.
        return max(5.0, min(parsed, 60.0))

    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']
        self.user = None

        try:
            # ======================================================================
            # SECURITY: Authenticate via Sec-WebSocket-Protocol subprotocols.
            # The token is NEVER read from the URL query string — query
            # strings are recorded in proxy access logs, browser history,
            # and Referer headers, and a long-lived DRF token must never
            # appear in a URL.
            # ======================================================================
            subprotocols = self.scope.get('subprotocols') or []
            token_key = None
            for proto in subprotocols:
                if not proto:
                    continue
                if proto.startswith('token.'):
                    token_key = proto[len('token.'):]
                    break
                if proto != 'token':
                    token_key = proto
                    break
            # Legacy: a single subprotocol that IS the token value
            if not token_key and len(subprotocols) == 1 and subprotocols[0] and subprotocols[0] != 'token':
                token_key = subprotocols[0]

            if not token_key:
                logger.warning(
                    "WebSocket connection rejected: No token subprotocol for "
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

            # Accept the WS with the negotiated 'token' subprotocol so
            # the client can verify the handshake honored the marker.
            # The actual auth key is never echoed in the response.
            await self.accept(subprotocol='token')
            self._accepted = True

            # ── INITIALIZE ──
            log_event(
                action="CONSOLE_SESSION_STARTED",
                target=f"Deployment: {self.deployment_id}",
                actor=self.user,
                metadata={
                    "container_id": self.container_id,
                    "user_id": str(self.user.id),
                    "user_email": self.user.email
                }
            )
            logger.info("[CONSOLE_DEBUG] TerminalConsumer connected: User %s, PID %s, deployment %s",
                        self.user, os.getpid(), self.deployment_id)

            # ── STATUS UPDATE: Immediate traffic after accept ──
            try:
                msg = '\r\n\x1b[36m[status] initializing stable tunnel...\x1b[0m\r\n\r\n'
                enc = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                await self._out_queue.put({'message': enc})
            except Exception:
                pass

            # ── TASK PIPELINE ──
            self._send_task = asyncio.create_task(self._send_loop())
            self._setup_task = asyncio.create_task(self._async_setup())
        except Exception as e:
            logger.error("[CONSOLE_DEBUG] TerminalConsumer.connect() failed: %s", e, exc_info=True)
            if self._accepted:
                try:
                    await self.send(text_data=json.dumps({'error': 'Internal error'}))
                except Exception:
                    pass
            await self.close(code=4000)

    async def _async_setup(self):
        """Background task to handle discovery and attachment without blocking handshake."""
        try:
            # Find the container
            self.container_id = await self._find_container()
            if not self.container_id:
                logger.error("Terminal connect: No container found for deployment %s", self.deployment_id)
                await self._out_queue.put({
                    'message': '\r\n\x1b[31m[error] No running container found for '
                               'this deployment.\x1b[0m\r\n'
                })
                return

            logger.info("Terminal connect: Found container %s for deployment %s", self.container_id, self.deployment_id)
            
            # Give the proxy a moment to settle the state
            await asyncio.sleep(0.5)

            # Start the exec session
            success = await self._start_exec()
            if not success:
                logger.error("Terminal connect: Failed to start exec in %s", self.container_id)
                await self._out_queue.put({
                    'message': '\r\n\x1b[31m[error] Failed to start shell in '
                               'container.\x1b[0m\r\n'
                })
                return

            logger.info("Terminal connect: Shell started in %s", self.container_id)

            # ── PRIME THE PIPE ──
            banner = (
                "\r\n\x1b[32m[connected to container]\x1b[0m\r\n"
                "\x1b[90m--------------------------------------------------\x1b[0m\r\n"
                f"\x1b[90mDeployment ID: {self.deployment_id}\x1b[0m\r\n"
                f"\x1b[90mContainer ID:  {self.container_id[:12]}\x1b[0m\r\n"
                "\x1b[90m--------------------------------------------------\x1b[0m\r\n\r\n"
            )
            encoded_banner = base64.b64encode(banner.encode('utf-8')).decode('utf-8')
            await self._out_queue.put({'message': encoded_banner})

            # Handshake ACK: Finalize the protocol upgrade for the proxy
            await self._out_queue.put({'type': 'pong'})

            # Force-trigger a prompt by sending a newline to the shell
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_to_shell, "\n")
            
            # Start reading output
            self._read_task = asyncio.create_task(self._read_output())
            
        except asyncio.CancelledError:
            logger.info("Terminal setup task cancelled")
        except Exception as e:
            logger.error("[CONSOLE_DEBUG] Error during terminal setup: %s", e, exc_info=True)
            msg = '\r\n\x1b[31m[error] internal proxy error\x1b[0m\r\n'
            enc = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
            await self._out_queue.put({'message': enc})
            await self.close()

    async def disconnect(self, code):
        self.is_disconnected = True
        logger.info(
            "WebSocket disconnected: User %s from deployment %s (code=%s)",
            getattr(self.user, 'id', 'Unknown'),
            self.deployment_id,
            code,
        )

        # ── CANCEL ALL TASKS ──
        tasks_to_cancel = [
            ('_setup_task', "setup"),
            ('_read_task', "read_output"),
            ('_send_task', "send_loop"),
        ]

        for attr, name in tasks_to_cancel:
            task = getattr(self, attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    # Brief wait for cancellation to propagate
                    await asyncio.wait_for(task, timeout=0.2)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.debug("[CONSOLE_DEBUG] %s task CANCELLED", name)
                except Exception as e:
                    logger.debug("Error cancelling %s task: %s", name, e)
                finally:
                    setattr(self, attr, None)

        self._close_exec_socket()

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

    async def receive(self, text_data=None, bytes_data=None):
        logger.info("[CONSOLE_DEBUG] receive() called: text_data=%s, bytes_data=%s", bool(text_data), bool(bytes_data))
        if text_data is None and bytes_data is not None:
            # We ignore binary frames since frontend only sends text
            return

        # SECURITY: Re-check authentication on each message
        if not self.user:
            logger.error("[CONSOLE_DEBUG] Closing 4001: Missing token"); await self.close(code=4001)
            return

        # 1. Handle structured JSON messages
        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                # Queue a pong response
                await self._out_queue.put({'type': 'pong'})
                return
            if data.get('type') == 'input' and data.get('payload'):
                # Base64 decode the payload representing raw client keys
                try:
                    text_data = base64.b64decode(data['payload']).decode('utf-8')
                except Exception:
                    # Ignore malformed packets gracefully
                    return
            elif isinstance(data, dict):
                # If it's any other JSON (like a pulse we sent or client sent 'ready'), drop it.
                logger.debug("Discarding non-input JSON message: %s", data)
                return
        except (json.JSONDecodeError, AttributeError, TypeError):
            # Fallback for old pure-text clients
            pass

        # ── COMMAND AUDIT BUFFERING ──
        # Buffer input and log when Enter (\r or \n) is pressed
        if text_data:
            for char in text_data:
                if char in ('\r', '\n'):
                    if self._cmd_buffer.strip():
                        log_event(
                            action="CONSOLE_COMMAND_EXECUTED",
                            target=f"Deployment: {self.deployment_id}",
                            actor=self.user,
                            metadata={
                                "command": self._cmd_buffer.strip(),
                                "container_id": self.container_id
                            }
                        )
                    self._cmd_buffer = ""
                elif ord(char) == 127: # Backspace
                    self._cmd_buffer = self._cmd_buffer[:-1]
                else:
                    self._cmd_buffer += char

        # Forward raw input to the container's exec stdin (via executor)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_to_shell, text_data)
        except Exception as e:
            logger.error("[CONSOLE_DEBUG] Error forwarding input to container: %s", e, exc_info=True)

    def _send_to_shell(self, data):
        """Blocking helper to send data to the container's raw socket."""
        raw = self._raw_sock or self.exec_socket
        if not raw:
            return
        try:
            if hasattr(raw, 'send'):
                raw.send(data.encode('utf-8'))
            elif hasattr(raw, 'write'):
                raw.write(data.encode('utf-8'))
        except Exception:
            pass

    async def _read_output(self):
        """Background task: read from exec socket and send to WebSocket.

        If the exec socket dies, attempts server-side reconnection up to
        3 times before giving up and closing the WebSocket.
        """
        loop = asyncio.get_event_loop()

        if not hasattr(self, '_last_activity'):
            self._last_activity = time.time()

        timeout_seconds = float(
            getattr(settings, "WEBSOCKET_IDLE_TIMEOUT", 420)
        )
        max_exec_reconnects = 10
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
                            msg = '\r\n\x1b[31m[session ended — exec reconnect limit reached]\x1b[0m\r\n'
                            enc_msg = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                            await self._out_queue.put({'message': enc_msg})
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
                        msg = f'\r\n\x1b[33m[exec disconnected — reconnecting {exec_reconnect_count}/{max_exec_reconnects}]\x1b[0m\r\n'
                        enc_msg = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                        await self._out_queue.put({'message': enc_msg})
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
                        msg = '\x1b[32m[reconnected to container]\x1b[0m\r\n'
                        enc_msg = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                        await self._out_queue.put({'message': enc_msg})
                    except Exception:
                        pass
                    continue

                if data == b'':
                    # Socket timeout or non-blocking empty return — check idle timeout
                    if time.time() - self._last_activity > timeout_seconds:
                        logger.info(
                            "Terminal idle timeout for %s",
                            self.deployment_id)
                        try:
                            msg = '\r\n\x1b[31m[idle timeout]\x1b[0m\r\n'
                            enc_msg = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                            await self.send(text_data=json.dumps({
                                'type': 'error',
                                'message': enc_msg
                            }))
                            await self.close(code=4000)
                        except Exception:
                            pass
                        break

                    # Avoid emitting empty frames on every socket timeout; the send-loop
                    # keepalive pong handles tunnel liveness with much lower frame churn.
                    # Yield briefly to prevent CPU spin.
                    await asyncio.sleep(0.5)
                    continue

                # Got real data — reset activity and exec reconnect counters
                if self.is_disconnected:
                    break

                self._last_activity = time.time()
                exec_reconnect_count = 0
                # Sanitize: strip NUL bytes for client/proxy compatibility
                text = data.decode('utf-8', errors='replace').replace('\x00', '')
                enc_text = base64.b64encode(text.encode('utf-8')).decode('utf-8')
                await self._out_queue.put({'message': enc_text})
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
        """Drains the output queue and sends it to the WebSocket."""
        logger.info("[CONSOLE_DEBUG] _send_loop task STARTED")
        try:
            # SECURITY: Wait for handshake to propagate before sending first frame
            while not self._accepted and not self.is_disconnected:
                await asyncio.sleep(0.1)

            # Conservative startup heartbeat, then steady keepalive.
            # This avoids frame flooding while still keeping proxies warm.
            start_time = time.time()
            while not self.is_disconnected:
                try:
                    # During the first few seconds, keep latency lower while tunnel settles.
                    # After that, use a less chatty keepalive to avoid reconnect churn.
                    current_duration = time.time() - start_time
                    wait_timeout = 5.0 if current_duration < 10.0 else 20.0
                    
                    msg = await asyncio.wait_for(self._out_queue.get(), timeout=wait_timeout)
                    
                    if not self.is_disconnected:
                        await self.send(text_data=json.dumps(msg))
                        await asyncio.sleep(0.01)
                
                except asyncio.TimeoutError:
                    # No data? Send a protocol-level 'pong' to keep the tunnel "hot"
                    if not self.is_disconnected:
                        try:
                            await self.send(text_data=json.dumps({'type': 'pong'}))
                        except Exception:
                            break
                
        except asyncio.CancelledError:
            logger.info("[CONSOLE_DEBUG] _send_loop task CANCELLED")
        except Exception as e:
            logger.error("[CONSOLE_DEBUG] _send_loop error (PID %s): %s", os.getpid(), e)
        finally:
            logger.info("[CONSOLE_DEBUG] _send_loop task TERMINATED")

    def _blocking_read(self):
        logger.debug("[CONSOLE_DEBUG] _blocking_read() started")
        """Blocking read from the exec socket. Runs in executor.

        Relies on the underlying docker-py socket timeout.
        """
        import socket

        sock = self._raw_sock or self.exec_socket
        if sock is None:
            return None

        try:
            if hasattr(sock, 'recv'):
                data = sock.recv(4096)
            elif hasattr(sock, 'read'):
                data = sock.read(4096)
            else:
                return None
            
            if not data:
                # True EOF
                return None
            return data
        except socket.timeout:
            # Timeout case - return empty heartbeat-equivalent
            return b''
        except Exception as e:
            err_name = e.__class__.__name__
            err_str = str(e).lower()
            if err_name == 'ReadTimeoutError' or 'timed out' in err_str or 'timeout' in err_str:
                return b''
            if 'connection broken' in err_str or 'chunkedencodingerror' in err_name.lower():
                return b''
            if 'bad file descriptor' in err_str or getattr(e, 'errno', None) == 9:
                return None
            logger.error("[CONSOLE_DEBUG] _blocking_read exception: %s", e, exc_info=True)
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

    async def _start_exec(self):
        """Create a docker exec instance and attach to it.

        Extracts the raw OS socket from docker-py's wrapper so that
        _blocking_read() can use recv() for reliable timeout behavior.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_start_exec)

    def _sync_start_exec(self):
        """Blocking part of exec start."""
        from apps.cloud.docker_client import get_docker_exec_client
        import socket as _socket
        try:
            client = get_docker_exec_client()
            container = client.containers.get(self.container_id)

            # Try bash first, fall back to sh
            shell = '/bin/bash'
            try:
                exit_code, _ = container.exec_run('which bash', demux=True)
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

            raw = getattr(self.exec_socket, '_sock', None)
            if raw is None:
                raw = self.exec_socket
            self._raw_sock = raw

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
        """Validate token and return user with enhanced error handling."""
        from rest_framework.authtoken.models import Token
        from django.core.cache import cache
        import hashlib
        
        if not token_key or not isinstance(token_key, str):
            logger.warning("Invalid token format: %s", type(token_key))
            return None
        
        # Basic token format validation (should be 40 chars hex)
        if len(token_key) != 40 or not all(c in '0123456789abcdef' for c in token_key):
            logger.warning("Token format validation failed: %s", token_key[:10] + '...')
            return None
        
        # Check for common invalid token patterns
        if token_key.startswith('0') * 40 or token_key == '0' * 40:
            logger.warning("Invalid token pattern detected")
            return None
        
        # Use cache to reduce database load for invalid tokens
        cache_key = f'invalid_token:{hashlib.sha256(token_key.encode()).hexdigest()}'
        if cache.get(cache_key):
            logger.info("Token found in invalid cache: %s", token_key[:10] + '...')
            return None
        
        try:
            token = Token.objects.select_related('user').get(key=token_key)
            
            # Additional validation: check if user is active
            if not token.user.is_active:
                logger.warning("User account inactive for token: %s", token_key[:10] + '...')
                cache.set(cache_key, True, 300)  # Cache for 5 minutes
                return None
            
            # Check if user has required permissions
            if not token.user.has_perm('deployments.view_deployment'):
                logger.warning("User lacks required permissions for token: %s", token_key[:10] + '...')
                cache.set(cache_key, True, 300)  # Cache for 5 minutes
                return None
            
            logger.debug("Token validated successfully for user: %s", token.user.username)
            return token.user
            
        except Token.DoesNotExist:
            logger.warning("Token not found: %s", token_key[:10] + '...')
            # Cache the invalid token to prevent repeated database hits
            cache.set(cache_key, True, 300)  # Cache for 5 minutes
            return None
        except Exception as e:
            logger.error("Error during token authentication: %s", e, exc_info=True)
            return None

    @database_sync_to_async
    def _verify_ownership(self):
        """Verify user owns the deployment, or is a member of the
        team that owns the service's project.

        SECURITY: previously this checked ``service.owner`` only. A
        team member who is removed from the team mid-session would
        retain their WebSocket — the existing query is run on every
        connect AND on every received message — so we must also
        reject the connection when the user is neither the owner
        nor a current team member. The membership check uses the
        ``TeamMember`` through table directly so a removed user
        immediately loses access on the next ``receive()`` call.
        """
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

        # ── ACCEPT IMMEDIATELY: Prevent proxy timeouts during auth ──
        await self.accept()

        try:
            # Authenticate
            query_string = self.scope.get('query_string', b'').decode()
            token_key = None
            for param in query_string.split('&'):
                if param.startswith('token='):
                    token_key = param.split('=', 1)[1]
                    break

            if not token_key:
                await self.send(text_data=json.dumps({'error': 'Missing token'}))
                await self.close(code=4001)
                return

            self.user = await self._authenticate_token(token_key)
            if not self.user:
                await self.send(text_data=json.dumps({'error': 'Invalid token'}))
                await self.close(code=4002)
                return

            if not await self._verify_ownership():
                await self.send(text_data=json.dumps({'error': 'Access denied'}))
                await self.close(code=4003)
                return

            # Join the deployment's log group
            self.group_name = f"build_logs_{self.deployment_id}"
            await self.channel_layer.group_add(
                self.group_name,
                self.channel_name
            )

            # Send current logs and status as initial payload
            initial = await self._get_current_state()
            await self.send(text_data=json.dumps({
                'type': 'initial_state',
                **initial
            }))
        except Exception as e:
            logger.error("[CONSOLE_DEBUG] BuildLogConsumer.connect() failed: %s", e, exc_info=True)
            try:
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            except Exception:
                pass
            await self.close(code=4000)

    async def disconnect(self, code):
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
        """Validate token and return user with enhanced error handling."""
        from rest_framework.authtoken.models import Token
        from django.core.cache import cache
        import hashlib
        
        if not token_key or not isinstance(token_key, str):
            logger.warning("Invalid token format: %s", type(token_key))
            return None
        
        # Basic token format validation (should be 40 chars hex)
        if len(token_key) != 40 or not all(c in '0123456789abcdef' for c in token_key):
            logger.warning("Token format validation failed: %s", token_key[:10] + '...')
            return None
        
        # Check for common invalid token patterns
        if token_key.startswith('0') * 40 or token_key == '0' * 40:
            logger.warning("Invalid token pattern detected")
            return None
        
        # Use cache to reduce database load for invalid tokens
        cache_key = f'invalid_token:{hashlib.sha256(token_key.encode()).hexdigest()}'
        if cache.get(cache_key):
            logger.info("Token found in invalid cache: %s", token_key[:10] + '...')
            return None
        
        try:
            token = Token.objects.select_related('user').get(key=token_key)
            
            # Additional validation: check if user is active
            if not token.user.is_active:
                logger.warning("User account inactive for token: %s", token_key[:10] + '...')
                cache.set(cache_key, True, 300)  # Cache for 5 minutes
                return None
            
            # Check if user has required permissions
            if not token.user.has_perm('deployments.view_deployment'):
                logger.warning("User lacks required permissions for token: %s", token_key[:10] + '...')
                cache.set(cache_key, True, 300)  # Cache for 5 minutes
                return None
            
            logger.debug("Token validated successfully for user: %s", token.user.username)
            return token.user
            
        except Token.DoesNotExist:
            logger.warning("Token not found: %s", token_key[:10] + '...')
            # Cache the invalid token to prevent repeated database hits
            cache.set(cache_key, True, 300)  # Cache for 5 minutes
            return None
        except Exception as e:
            logger.error("Error during token authentication: %s", e, exc_info=True)
            return None

    @database_sync_to_async
    def _verify_ownership(self):
        # SECURITY: also accept team members of the service's project,
        # not just the service owner. See TerminalConsumer._verify_ownership
        # for the full rationale.
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
        """Fetch current build logs and status for initial load."""
        from apps.deployments.models import Deployment
        try:
            d = Deployment.objects.get(id=self.deployment_id)
            # Sanitize existing logs for the initial state broadcast
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


class ServiceStatusConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time service status updates.
    
    Connects to channel groups per user and broadcasts service status changes
    as they happen. Services update their status via channel_layer.
    
    Usage:
        ws://host/ws/service-status/?token=xxx
    
    Messages sent to client:
        {
            "type": "service_status_update",
            "service_id": "uuid",
            "service_name": "name",
            "status": "ACTIVE|FAILED|DELETION_PENDING...",
            "deployment_status": "ACTIVE|FAILED|...",
            "updated_at": "2026-05-21T12:00:00Z"
        }
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None
        self.user_group_name = None

    async def connect(self):
        """Authenticate and join user's service status group."""
        # ── ACCEPT IMMEDIATELY: Prevent proxy timeouts during auth ──
        await self.accept()

        try:
            # Authenticate
            query_string = self.scope.get('query_string', b'').decode()
            token_key = None
            for param in query_string.split('&'):
                if param.startswith('token='):
                    token_key = param.split('=', 1)[1]
                    break

            if not token_key:
                await self.send(text_data=json.dumps({'error': 'Missing token'}))
                await self.close(code=4001)
                return

            self.user = await self._authenticate_token(token_key)
            if not self.user:
                await self.send(text_data=json.dumps({'error': 'Invalid token'}))
                await self.close(code=4002)
                return

            # Join the user's service status group
            self.user_group_name = f"user_services_{self.user.id}"
            await self.channel_layer.group_add(
                self.user_group_name,
                self.channel_name
            )

            # Send initial service statuses
            await self._send_initial_services()
        except Exception as e:
            logger.error("[CONSOLE_DEBUG] ServiceStatusConsumer.connect() failed: %s", e, exc_info=True)
            try:
                await self.send(text_data=json.dumps({'error': 'Internal error'}))
            except Exception:
                pass
            await self.close(code=4000)

    async def disconnect(self, code):
        """Leave group on disconnect."""
        if self.user_group_name:
            await self.channel_layer.group_discard(
                self.user_group_name,
                self.channel_name
            )

    async def receive(self, text_data=None, bytes_data=None):
        """Handle incoming messages (typically pings)."""
        if text_data:
            try:
                data = json.loads(text_data)
                if data.get('type') == 'ping':
                    await self.send(text_data=json.dumps({'type': 'pong'}))
            except json.JSONDecodeError:
                pass

    async def service_status_update(self, event):
        """Handle service status update broadcast."""
        await self.send(text_data=json.dumps({
            'type': 'service_status_update',
            'service_id': event['service_id'],
            'service_name': event['service_name'],
            'status': event['status'],
            'deployment_status': event.get('deployment_status', 'unknown'),
            'updated_at': event.get('updated_at', ''),
        }))

    async def deployment_status_update(self, event):
        """Handle deployment status update broadcast."""
        await self.send(text_data=json.dumps({
            'type': 'deployment_status_update',
            'service_id': event['service_id'],
            'service_name': event['service_name'],
            'deployment_id': event['deployment_id'],
            'status': event['status'],
            'updated_at': event.get('updated_at', ''),
        }))

    @database_sync_to_async
    def _authenticate_token(self, token_key):
        """Validate token and return user with enhanced error handling."""
        from rest_framework.authtoken.models import Token
        from django.core.cache import cache
        import hashlib
        
        if not token_key or not isinstance(token_key, str):
            logger.warning("Invalid token format: %s", type(token_key))
            return None
        
        # Basic token format validation (should be 40 chars hex)
        if len(token_key) != 40 or not all(c in '0123456789abcdef' for c in token_key):
            logger.warning("Token format validation failed: %s", token_key[:10] + '...')
            return None
        
        # Check for common invalid token patterns
        if token_key.startswith('0') * 40 or token_key == '0' * 40:
            logger.warning("Invalid token pattern detected")
            return None
        
        # Use cache to reduce database load for invalid tokens
        cache_key = f'invalid_token:{hashlib.sha256(token_key.encode()).hexdigest()}'
        if cache.get(cache_key):
            logger.info("Token found in invalid cache: %s", token_key[:10] + '...')
            return None
        
        try:
            token = Token.objects.select_related('user').get(key=token_key)
            
            # Additional validation: check if user is active
            if not token.user.is_active:
                logger.warning("User account inactive for token: %s", token_key[:10] + '...')
                cache.set(cache_key, True, 300)  # Cache for 5 minutes
                return None
            
            # Check if user has required permissions
            if not token.user.has_perm('deployments.view_deployment'):
                logger.warning("User lacks required permissions for token: %s", token_key[:10] + '...')
                cache.set(cache_key, True, 300)  # Cache for 5 minutes
                return None
            
            logger.debug("Token validated successfully for user: %s", token.user.username)
            return token.user
            
        except Token.DoesNotExist:
            logger.warning("Token not found: %s", token_key[:10] + '...')
            # Cache the invalid token to prevent repeated database hits
            cache.set(cache_key, True, 300)  # Cache for 5 minutes
            return None
        except Exception as e:
            logger.error("Error during token authentication: %s", e, exc_info=True)
            return None

    @database_sync_to_async
    def _get_user_services(self):
        """Get all services for the authenticated user."""
        from apps.deployments.models import Service, Deployment
        services = Service.objects.filter(owner=self.user).prefetch_related(
            'deployments', 'deployments__service'
        )
        
        # Get the latest deployment for each service
        services_with_status = []
        for service in services:
            latest_deployment = service.deployments.order_by('-created_at').first()
            services_with_status.append({
                'id': str(service.id),
                'name': service.name,
                'status': service.status,
                'deployment_status': latest_deployment.status if latest_deployment else 'unknown',
                'updated_at': service.updated_at.isoformat() if service.updated_at else None,
            })
        
        return services_with_status

    async def _send_initial_services(self):
        """Send initial service statuses to the client."""
        try:
            services = await self._get_user_services()
            for service in services:
                await self.send(text_data=json.dumps({
                    'type': 'service_status_update',
                    'service_id': service['id'],
                    'service_name': service['name'],
                    'status': service['status'],
                    'deployment_status': service['deployment_status'],
                    'updated_at': service['updated_at'],
                }))
        except Exception as e:
            logger.error("Error sending initial service statuses: %s", e)
