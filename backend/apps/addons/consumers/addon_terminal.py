"""Interactive terminal (console) for addon containers.

Mirrors the service-container console (TerminalConsumer) but targets an
addon's backing container — including the per-service SHARED container
used by CLI addon types.

Sandbox model (same as the service console — no new trust invented):
- The container itself is the sandbox: dropped capabilities,
  no-new-privileges, tenant-scoped networks only.
- The exec runs with the container default user (root, like the service
  console and plain ``docker exec``) because CLI configs, npm-global
  installs and data volumes live in root-owned paths; a non-root shell
  could not operate the CLIs.
- Access is gated on service write permission (owner / team member /
  superuser) with the same WS-token auth as the service console, every
  session is audit-logged (ADDON_CONSOLE_SESSION_STARTED/ENDED), and
  the pre-existing idle keepalive disconnects stale sockets.
- Soft-deleted or missing addons, and stopped/missing containers, are
  refused with a clear in-terminal message instead of hanging.
"""
import asyncio
import base64
import contextlib
import json
import logging
import time

from channels.db import database_sync_to_async
from django.conf import settings

from apps.deployments.consumers.base import get_websocket_subprotocol
from apps.deployments.consumers.terminal import TerminalConsumer

logger = logging.getLogger(__name__)


class AddonTerminalConsumer(TerminalConsumer):
    """Interactive shell in an addon's backing container (sandboxed)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.addon_id = None

    async def connect(self):
        self.addon_id = self.scope['url_route']['kwargs']['addon_id']
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
                    "Addon terminal rejected: No token subprotocol for "
                    "addon %s", self.addon_id)
                await self.close(code=4001)
                return

            self.user = await self._authenticate_token(token_key)
            if not self.user:
                scope_user = self.scope.get('user')
                if (scope_user is not None
                        and getattr(scope_user, 'is_authenticated', False)
                        and getattr(scope_user, 'is_active', False)):
                    self.user = scope_user
            if not self.user:
                logger.warning(
                    "Addon terminal rejected: Invalid token for "
                    "addon %s", self.addon_id)
                await self.close(code=4002)
                return

            if not await self._verify_ownership():
                logger.warning(
                    "Addon terminal rejected: User %s doesn't own "
                    "addon %s", self.user.id, self.addon_id)
                await self.close(code=4003)
                return

            await self.accept(subprotocol=get_websocket_subprotocol(self.scope))
            self._accepted = True

            from asgiref.sync import sync_to_async
            from apps.deployments.utils import log_event
            await sync_to_async(log_event)(
                action="ADDON_CONSOLE_SESSION_STARTED",
                target=f"Addon: {self.addon_id}",
                actor=self.user,
                metadata={
                    "container_id": self.container_id,
                    "user_id": str(self.user.id),
                    "user_email": self.user.email
                }
            )

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
                logger.error("AddonTerminalConsumer.connect() failed: %s", e, exc_info=True)
            if self._accepted:
                with contextlib.suppress(Exception):
                    await self.send(text_data=json.dumps({'error': 'Internal error'}))
            await self.close(code=4000)

    async def _async_setup(self):
        try:
            self.container_id = await self._find_container()
            if not self.container_id:
                logger.error("Addon terminal: No container found for addon %s", self.addon_id)
                await self._out_queue.put({
                    'message': '\r\n\x1b[31m[error] No running container found for '
                               'this addon (deleted, or not provisioned yet).\x1b[0m\r\n'
                })
                return

            logger.info("Addon terminal: Found container %s for addon %s", self.container_id, self.addon_id)
            await asyncio.sleep(0.5)

            success = await self._start_exec()
            if not success:
                logger.error("Addon terminal: Failed to start exec in %s", self.container_id)
                await self._out_queue.put({
                    'message': '\r\n\x1b[31m[error] Failed to start shell in '
                               'container.\x1b[0m\r\n'
                })
                return

            logger.info("Addon terminal: Shell started in %s", self.container_id)

            banner = (
                "\r\n\x1b[32m[connected to addon container — sandboxed]\x1b[0m\r\n"
                "\x1b[90m--------------------------------------------------\x1b[0m\r\n"
                f"\x1b[90mAddon ID:     {self.addon_id}\x1b[0m\r\n"
                f"\x1b[90mContainer ID: {self.container_id[:12]}\x1b[0m\r\n"
                "\x1b[90m--------------------------------------------------\x1b[0m\r\n\r\n"
            )
            encoded_banner = base64.b64encode(banner.encode('utf-8')).decode('utf-8')
            await self._out_queue.put({'message': encoded_banner})

            await self._out_queue.put({'type': 'pong'})

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_to_shell, "\n")

            self._read_task = asyncio.create_task(self._read_output())
        except asyncio.CancelledError:
            logger.info("Addon terminal setup task cancelled")
        except Exception as e:
            if settings.DEBUG:
                logger.error("Error during addon terminal setup: %s", e, exc_info=True)
            msg = '\r\n\x1b[31m[error] internal proxy error\x1b[0m\r\n'
            enc = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
            await self._out_queue.put({'message': enc})
            await self.close()

    async def disconnect(self, code):
        self.is_disconnected = True
        logger.info(
            "WebSocket disconnected: User %s from addon %s (code=%s)",
            getattr(self.user, 'id', 'Unknown'),
            self.addon_id,
            code,
        )
        try:
            from asgiref.sync import sync_to_async
            from apps.deployments.utils import log_event
            if self.user is not None and self._accepted:
                await sync_to_async(log_event)(
                    action="ADDON_CONSOLE_SESSION_ENDED",
                    target=f"Addon: {self.addon_id}",
                    actor=self.user,
                    metadata={"code": code},
                )
        except Exception as exc:
            logger.debug("Addon console audit write failed: %s", exc)

        for attr in ('_setup_task', '_read_task', '_send_task'):
            task = getattr(self, attr, None)
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=0.2)
                except (TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as e:
                    if settings.DEBUG:
                        logger.debug("task teardown issue: %s", e)

    async def _find_container(self):
        # Must stay awaitable — the base _async_setup / _read_output
        # paths ``await`` it. The heavy lifting runs in a thread via
        # database_sync_to_async (ORM + blocking docker calls).
        return await _find_addon_container(self.addon_id)

    async def _audit_command(self, command: str) -> None:
        """Persist one executed command without blocking the input path."""
        try:
            from asgiref.sync import sync_to_async
            from apps.deployments.utils import log_event
            await sync_to_async(log_event)(
                action="ADDON_CONSOLE_COMMAND_EXECUTED",
                target=f"Addon: {self.addon_id}",
                actor=self.user,
                metadata={
                    "command": command,
                    "container_id": self.container_id,
                },
            )
        except Exception as exc:
            logger.debug("Addon console audit write failed: %s", exc)

    async def _verify_ownership(self):
        from apps.deployments.models.addons import Addon
        try:
            addon = await _get_addon(self.addon_id)
            if addon is None:
                return False
            if self.user.is_superuser:
                return True
            if addon.service.owner_id == self.user.id:
                return True
            project = getattr(addon.service, 'project', None)
            if project is not None and project.team_id is not None:
                return await _is_team_member(project.team_id, self.user.id)
            return False
        except Exception:
            return False


@database_sync_to_async
def _find_addon_container(addon_id):
    """Resolve a RUNNING backing container id for an addon (None if refused)."""
    from apps.cloud.docker_client import get_docker_exec_client
    from apps.deployments.models.addons import Addon
    try:
        addon = Addon.objects.select_related('service').get(id=addon_id)
        if str(getattr(addon, 'status', '')) in ('DELETED', 'DELETION_PENDING', 'DELETION_FAILED'):
            logger.warning("Addon terminal refused: addon %s is %s",
                           addon_id, addon.status)
            return None
        try:
            from apps.addons.services.addon_provisioner import addon_container_name
            name = addon_container_name(addon)
        except Exception:
            name = (f"smsly-addon-{str(getattr(addon, 'addon_type', '')).lower()}-"
                    f"{getattr(addon, 'id', '')}")
        client = get_docker_exec_client()
        try:
            container = client.containers.get(name)
            container.reload()
            if getattr(container, 'status', '') == 'running':
                return container.id
            logger.warning("Addon terminal refused: container %s is %s",
                           name, getattr(container, 'status', 'unknown'))
            return None
        except Exception:
            logger.warning("Addon terminal: container %s not found", name)
            return None
    except Addon.DoesNotExist:
        return None
    except Exception as e:
        logger.error("Error finding addon container: %s", e)
        return None


@database_sync_to_async
def _get_addon(addon_id):
    from apps.deployments.models.addons import Addon
    try:
        return Addon.objects.select_related('service', 'service__owner').get(id=addon_id)
    except Addon.DoesNotExist:
        return None


@database_sync_to_async
def _is_team_member(team_id, user_id):
    """Mirror teams.permissions membership semantics (active, unexpired)."""
    try:
        from django.utils import timezone
        from apps.teams.models import TeamMember
        return TeamMember.objects.filter(
            team_id=team_id, user_id=user_id, is_active=True,
        ).exclude(
            expires_at__isnull=False, expires_at__lt=timezone.now(),
        ).exists()
    except Exception:
        return False
