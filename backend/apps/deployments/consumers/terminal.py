"""WebSocket consumer for interactive terminal access to containers."""
import asyncio
import base64
import contextlib
import json
import logging
import os
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from .base import authenticate_ws_token, get_websocket_subprotocol, verify_deployment_ownership

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
        self._raw_sock = None
        self._read_task = None
        self._send_task = None
        self._setup_task = None
        self._cmd_buffer = ""
        self._out_queue = asyncio.Queue()
        self.is_disconnected = False
        self._accepted = False
        self._last_activity = time.time()
        self._keepalive_timeout_seconds = self._resolve_keepalive_timeout()

    def _resolve_keepalive_timeout(self) -> float:
        raw_value = os.getenv("TERMINAL_WS_KEEPALIVE_SECONDS", "20")
        try:
            parsed = float(raw_value)
        except (TypeError, ValueError):
            parsed = 20.0
        return max(5.0, min(parsed, 60.0))

    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']
        self.user = None

        try:
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
            if not token_key and len(subprotocols) == 1 and subprotocols[0] and subprotocols[0] != 'token':
                token_key = subprotocols[0]

            if not token_key:
                logger.warning(
                    "WebSocket connection rejected: No token subprotocol for "
                    "deployment %s", self.deployment_id)
                await self.close(code=4001)
                return

            self.user = await self._authenticate_token(token_key)
            if not self.user:
                logger.warning(
                    "WebSocket connection rejected: Invalid token for "
                    "deployment %s", self.deployment_id)
                await self.close(code=4002)
                return

            if not await self._verify_ownership():
                logger.warning(
                    "WebSocket connection rejected: User %s doesn't own "
                    "deployment %s", self.user.id, self.deployment_id)
                await self.close(code=4003)
                return

            await self.accept(subprotocol=get_websocket_subprotocol(self.scope))
            self._accepted = True

            from asgiref.sync import sync_to_async
            from apps.deployments.utils import log_event
            await sync_to_async(log_event)(
                action="CONSOLE_SESSION_STARTED",
                target=f"Deployment: {self.deployment_id}",
                actor=self.user,
                metadata={
                    "container_id": self.container_id,
                    "user_id": str(self.user.id),
                    "user_email": self.user.email
                }
            )
            if settings.DEBUG:
                logger.info("TerminalConsumer connected: deployment %s", self.deployment_id)

            try:
                msg = '\r\n\x1b[36m[status] initializing stable tunnel...\x1b[0m\r\n\r\n'
                enc = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                await self._out_queue.put({'message': enc})
            except Exception as exc:
                logger.debug("Failed to send init message: %s", exc)

            self._send_task = asyncio.create_task(self._send_loop())
            self._setup_task = asyncio.create_task(self._async_setup())
        except Exception as e:
            if settings.DEBUG:
                logger.error("TerminalConsumer.connect() failed: %s", e, exc_info=True)
            if self._accepted:
                with contextlib.suppress(Exception):
                    await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def _async_setup(self):
        try:
            self.container_id = await self._find_container()
            if not self.container_id:
                logger.error("Terminal connect: No container found for deployment %s", self.deployment_id)
                await self._out_queue.put({
                    'message': '\r\n\x1b[31m[error] No running container found for '
                               'this deployment.\x1b[0m\r\n'
                })
                return

            logger.info("Terminal connect: Found container %s for deployment %s", self.container_id, self.deployment_id)
            await asyncio.sleep(0.5)

            success = await self._start_exec()
            if not success:
                logger.error("Terminal connect: Failed to start exec in %s", self.container_id)
                await self._out_queue.put({
                    'message': '\r\n\x1b[31m[error] Failed to start shell in '
                               'container.\x1b[0m\r\n'
                })
                return

            logger.info("Terminal connect: Shell started in %s", self.container_id)

            banner = (
                "\r\n\x1b[32m[connected to container]\x1b[0m\r\n"
                "\x1b[90m--------------------------------------------------\x1b[0m\r\n"
                f"\x1b[90mDeployment ID: {self.deployment_id}\x1b[0m\r\n"
                f"\x1b[90mContainer ID:  {self.container_id[:12]}\x1b[0m\r\n"
                "\x1b[90m--------------------------------------------------\x1b[0m\r\n\r\n"
            )
            encoded_banner = base64.b64encode(banner.encode('utf-8')).decode('utf-8')
            await self._out_queue.put({'message': encoded_banner})

            await self._out_queue.put({'type': 'pong'})

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_to_shell, "\n")

            self._read_task = asyncio.create_task(self._read_output())
        except asyncio.CancelledError:
            logger.info("Terminal setup task cancelled")
        except Exception as e:
            if settings.DEBUG:
                logger.error("Error during terminal setup: %s", e, exc_info=True)
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
                    await asyncio.wait_for(task, timeout=0.2)
                except (TimeoutError, asyncio.CancelledError):
                    if settings.DEBUG:
                        logger.debug("%s task CANCELLED", name)
                except Exception as e:
                    logger.debug("Error cancelling %s task: %s", name, e)
                finally:
                    setattr(self, attr, None)

        self._close_exec_socket()

    def _close_exec_socket(self):
        for sock in (self._raw_sock, self.exec_socket):
            if sock is None:
                continue
            with contextlib.suppress(Exception):
                sock.close()
        self._raw_sock = None
        self.exec_socket = None
        self.exec_id = None

    async def receive(self, text_data=None, bytes_data=None):
        if settings.DEBUG:
            logger.info("receive() called: text_data=%s, bytes_data=%s", bool(text_data), bool(bytes_data))
        if text_data is None and bytes_data is not None:
            return

        if not self.user:
            if settings.DEBUG:
                logger.error("Closing 4001: Missing token")
                await self.close(code=4001)
            return

        try:
            data = json.loads(text_data)
            if data.get('type') == 'ping':
                await self._out_queue.put({'type': 'pong'})
                return
            if data.get('type') == 'input' and data.get('payload'):
                try:
                    text_data = base64.b64decode(data['payload']).decode('utf-8')
                except Exception:
                    return
            elif isinstance(data, dict):
                logger.debug("Discarding non-input JSON message: %s", data)
                return
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        if text_data:
            for char in text_data:
                if char in ('\r', '\n'):
                    if self._cmd_buffer.strip():
                        from asgiref.sync import sync_to_async
                        from apps.deployments.utils import log_event
                        await sync_to_async(log_event)(
                            action="CONSOLE_COMMAND_EXECUTED",
                            target=f"Deployment: {self.deployment_id}",
                            actor=self.user,
                            metadata={
                                "command": self._cmd_buffer.strip(),
                                "container_id": self.container_id
                            }
                        )
                    self._cmd_buffer = ""
                elif ord(char) == 127:
                    self._cmd_buffer = self._cmd_buffer[:-1]
                else:
                    self._cmd_buffer += char

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_to_shell, text_data)
        except Exception as e:
            if settings.DEBUG:
                logger.error("Error forwarding input to container: %s", e, exc_info=True)

    def _send_to_shell(self, data):
        raw = self._raw_sock or self.exec_socket
        if not raw:
            return
        try:
            if hasattr(raw, 'send'):
                raw.send(data.encode('utf-8'))
            elif hasattr(raw, 'write'):
                raw.write(data.encode('utf-8'))
        except Exception as exc:
            logger.debug("Failed to write to exec socket: %s", exc)

    async def _read_output(self):
        loop = asyncio.get_running_loop()

        if not hasattr(self, '_last_activity'):
            self._last_activity = time.time()

        timeout_seconds = float(
            getattr(settings, "WEBSOCKET_IDLE_TIMEOUT", 420)
        )
        max_exec_reconnects = 10
        exec_reconnect_count = 0

        try:
            while True:
                try:
                    data = await asyncio.wait_for(
                        loop.run_in_executor(None, self._blocking_read),
                        timeout=20.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Terminal read timed out")
                    return

                if data is None:
                    exec_reconnect_count += 1
                    if exec_reconnect_count > max_exec_reconnects:
                        logger.info(
                            "Terminal exec reconnect limit reached for %s",
                            self.deployment_id)
                        try:
                            msg = '\r\n\x1b[31m[session ended — exec reconnect limit reached]\x1b[0m\r\n'
                            enc_msg = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                            await self._out_queue.put({'message': enc_msg})
                            await asyncio.sleep(0.5)
                            await self.close(code=4000)
                        except Exception as exc:
                            logger.debug("Failed to send reconnect limit message: %s", exc)
                        break

                    logger.info(
                        "Terminal exec socket died for %s, reconnecting "
                        "(%d/%d)", self.deployment_id,
                        exec_reconnect_count, max_exec_reconnects)
                    try:
                        msg = '\r\n\x1b[33m[exec disconnected — reconnecting {}/{}]\x1b[0m\r\n'.format(
                            exec_reconnect_count, max_exec_reconnects)
                        enc_msg = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
                        await self._out_queue.put({'message': enc_msg})
                    except Exception as exc:
                        logger.debug("Failed to send reconnecting message: %s", exc)

                    self._close_exec_socket()
                    await asyncio.sleep(1.0)

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
                    except Exception as exc:
                        logger.debug("Failed to send reconnected message: %s", exc)
                    continue

                if data == b'':
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
                        except Exception as exc:
                            logger.debug("Failed to send idle timeout message: %s", exc)
                        break
                    await asyncio.sleep(0.5)
                    continue

                if self.is_disconnected:
                    break

                self._last_activity = time.time()
                exec_reconnect_count = 0
                text = data.decode('utf-8', errors='replace').replace('\x00', '')
                enc_text = base64.b64encode(text.encode('utf-8')).decode('utf-8')
                await self._out_queue.put({'message': enc_text})
        except asyncio.CancelledError:
            if settings.DEBUG:
                logger.info("_read_output task CANCELLED")
        except Exception as e:
            if not self.is_disconnected and settings.DEBUG:
                logger.error("_read_output error: %s", e, exc_info=True)
            with contextlib.suppress(Exception):
                await self.close()
        finally:
            if settings.DEBUG:
                logger.info("_read_output task TERMINATED")

    async def _send_loop(self):
        if settings.DEBUG:
            logger.info("_send_loop task STARTED")
        try:
            while not self._accepted and not self.is_disconnected:
                await asyncio.sleep(0.1)

            start_time = time.time()
            while not self.is_disconnected:
                try:
                    current_duration = time.time() - start_time
                    wait_timeout = 5.0 if current_duration < 10.0 else 20.0

                    msg = await asyncio.wait_for(self._out_queue.get(), timeout=wait_timeout)

                    if not self.is_disconnected:
                        await self.send(text_data=json.dumps(msg))
                        await asyncio.sleep(0.01)
                except TimeoutError:
                    if not self.is_disconnected:
                        try:
                            await self.send(text_data=json.dumps({'type': 'pong'}))
                        except Exception:
                            break
        except asyncio.CancelledError:
            if settings.DEBUG:
                logger.info("_send_loop task CANCELLED")
        except Exception as e:
            if settings.DEBUG:
                logger.error("_send_loop error: %s", e)
        finally:
            if settings.DEBUG:
                logger.info("_send_loop task TERMINATED")

    def _blocking_read(self):
        if settings.DEBUG:
            logger.debug("_blocking_read() started")

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
                return None
            return data
        except TimeoutError:
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
            if settings.DEBUG:
                logger.error("_blocking_read exception: %s", e, exc_info=True)
            logger.error(
                "Terminal _blocking_read exception for %s: %s - %s",
                self.deployment_id, type(e), e)
            return None

    @database_sync_to_async
    def _find_container(self):
        from apps.cloud.docker_client import get_docker_exec_client
        from apps.deployments.models import Deployment
        try:
            dep = Deployment.objects.select_related('service').get(
                id=self.deployment_id)
            service_name = dep.service.name

            client = get_docker_exec_client()
            containers = client.containers.list(
                filters={'name': service_name, 'status': 'running'})
            if containers:
                return containers[0].id

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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_start_exec)

    def _sync_start_exec(self):
        from apps.cloud.docker_client import get_docker_exec_client
        try:
            client = get_docker_exec_client()
            container = client.containers.get(self.container_id)

            shell = '/bin/bash'
            try:
                exit_code, _ = container.exec_run('which bash', demux=True)
                if exit_code != 0:
                    shell = '/bin/sh'
            except Exception:
                shell = '/bin/sh'

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
            except (OSError, AttributeError) as e:
                logger.warning("Could not set exec socket timeout: %s", e)

            return True
        except Exception as e:
            logger.error("Error starting exec: %s", e)
            return False

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)

    async def _verify_ownership(self):
        return await verify_deployment_ownership(self.user, self.deployment_id)
