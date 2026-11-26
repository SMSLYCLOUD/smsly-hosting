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

from apps.deployments.consumers.base import authenticate_ws_credential, get_websocket_subprotocol
from apps.deployments.consumers.terminal import TerminalConsumer

logger = logging.getLogger(__name__)


class AddonTerminalConsumer(TerminalConsumer):
    """Interactive shell in an addon's backing container (sandboxed)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.addon_id = None
        self.api_token = None

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

            if token_key:
                if type(self)._authenticate_token is TerminalConsumer._authenticate_token:
                    self.user, self.api_token = await authenticate_ws_credential(token_key)
                else:
                    self.user = await self._authenticate_token(token_key)
                    self.api_token = None
                if not self.user:
                    logger.warning(
                        "Addon terminal rejected: Invalid token for "
                        "addon %s", self.addon_id)
                    await self.close(code=4002)
                    return
            else:
                scope_user = self.scope.get('user')
                if (
                    scope_user is not None
                    and getattr(scope_user, 'is_authenticated', False)
                    and getattr(scope_user, 'is_active', False)
                ):
                    self.user = scope_user
                    self.api_token = self.scope.get('api_token')
                if not self.user:
                    logger.warning(
                        "Addon terminal rejected: Unauthenticated connection for "
                        "addon %s", self.addon_id)
                    await self.close(code=4001)
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
        setup_completed = False
        try:
            self.container_id = await asyncio.wait_for(self._find_container(), timeout=30)
            if not self.container_id:
                logger.error("Addon terminal: No container found for addon %s", self.addon_id)
                await self._out_queue.put({
                    'message': '\r\n\x1b[31m[error] No running container found for '
                               'this addon (deleted, or not provisioned yet).\x1b[0m\r\n'
                })
                return

            logger.info("Addon terminal: Found container %s for addon %s", self.container_id, self.addon_id)
            await asyncio.sleep(0.5)

            success = await asyncio.wait_for(self._start_exec(), timeout=30)
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
            setup_completed = True
        except asyncio.CancelledError:
            logger.info("Addon terminal setup task cancelled")
            raise
        except Exception as e:
            if settings.DEBUG:
                logger.error("Error during addon terminal setup: %s", e, exc_info=True)
            msg = '\r\n\x1b[31m[error] internal proxy error\x1b[0m\r\n'
            enc = base64.b64encode(msg.encode('utf-8')).decode('utf-8')
            await self._out_queue.put({'message': enc})
            await self.close()
        finally:
            if not setup_completed and (self.exec_id or self.exec_socket or self._raw_sock):
                await self._close_exec_socket()

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

        try:
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
        finally:
            await self._close_exec_socket()

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
            sanitized = self._truncate_command(self._redact_command(command))
            await sync_to_async(log_event)(
                action="ADDON_CONSOLE_COMMAND_EXECUTED",
                target=f"Addon: {self.addon_id}",
                actor=self.user,
                metadata={
                    "command": sanitized,
                    "container_id": self.container_id,
                },
            )
        except Exception as exc:
            logger.debug("Addon console audit write failed: %s", exc)

    async def _verify_ownership(self):
        try:
            addon = await _get_addon(self.addon_id)
            if addon is None or addon.service is None:
                return False
            api_token = getattr(self, 'api_token', None)
            if api_token is not None:
                if not api_token.has_scope('write'):
                    return False
                if not api_token.has_project(addon.service.project_id):
                    return False
            if getattr(self.user, 'is_superuser', False):
                return True
            from apps.teams.permissions import user_can_write
            return await database_sync_to_async(user_can_write)(self.user, addon.service)
        except Exception:
            return False


_PLATFORM_ADDON_BLOCKLIST = (
    'smsly-shared-postgres',
    'pgbouncer-tenants',
    'smsly-hosting',
    'smsly-spire',
    'smsly-edge',
    'smsly-caddy',
    'smsly-traefik',
    'smsly-loki',
    'smsly-falco',
    'smsly-crowdsec',
    'smsly-appsec',
    'smsly-cloudflare',
    'socket-proxy',
)


def _is_platform_container_name(name: str) -> bool:
    norm = str(name or '').strip().lower().lstrip('/')
    if not norm:
        return True
    return norm in _PLATFORM_ADDON_BLOCKLIST or norm.startswith(_PLATFORM_ADDON_BLOCKLIST)


@database_sync_to_async
def _find_addon_container(addon_id):
    """Resolve a RUNNING backing container id for an addon (None if refused)."""
    from apps.cloud.docker_client import get_docker_exec_client
    from apps.deployments.models.addons import Addon
    try:
        addon = Addon.objects.select_related('service').get(id=addon_id)
        if str(getattr(addon, 'provision_mode', '') or '') == 'shared' or bool(getattr(addon, 'pooler_routed', False)):
            logger.warning("Addon terminal refused: shared addon %s has no dedicated container", addon_id)
            return None
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
        if _is_platform_container_name(name):
            logger.warning("Addon terminal refused: platform container %s for addon %s", name, addon_id)
            return None
        client = get_docker_exec_client()
        try:
            container = client.containers.get(name)
            container.reload()
            if getattr(container, 'status', '') != 'running':
                logger.warning("Addon terminal refused: container %s is %s",
                               name, getattr(container, 'status', 'unknown'))
                return None
            actual_name = getattr(container, 'name', '')
            if isinstance(actual_name, str) and actual_name and _is_platform_container_name(actual_name):
                logger.warning("Addon terminal refused: resolved container %s is platform-owned", actual_name)
                return None
            labels = getattr(container, 'labels', None)
            if not isinstance(labels, dict):
                attrs_cfg = (getattr(container, 'attrs', None) or {}).get('Config')
                labels = attrs_cfg.get('Labels') if isinstance(attrs_cfg, dict) else None
            if isinstance(labels, dict):
                if str(labels.get('com.docker.compose.project') or '').lower().startswith('smsly'):
                    logger.warning("Addon terminal refused: platform compose project on %s", name)
                    return None
                from apps.addons.services import cli_addons as _cli
                if _cli.is_cli_addon(getattr(addon, 'addon_type', '')):
                    if labels.get('smsly.service_id') and str(labels.get('smsly.service_id')) != str(addon.service_id):
                        logger.warning("Addon terminal refused: service label mismatch on %s", name)
                        return None
                elif str(labels.get('smsly.addon_id') or '') != str(addon.id):
                    logger.warning("Addon terminal refused: smsly.addon_id mismatch on %s", name)
                    return None
            return container.id
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
        return Addon.objects.select_related(
            'service', 'service__owner', 'service__project__team'
        ).get(id=addon_id)
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
