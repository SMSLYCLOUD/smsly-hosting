"""Real-time platform update progress and terminal log streaming consumer."""
import base64
import contextlib
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .base import (
    authenticate_ws_credential,
    authenticate_ws_token,
    get_websocket_subprotocol,
    logger,
)


def token_has_scope(api_token, scope: str) -> bool:
    """Return True if *api_token* grants *scope* (legacy unscoped tokens allow all)."""
    if api_token is None:
        return True
    if hasattr(api_token, 'has_scope'):
        return bool(api_token.has_scope(scope))
    scopes = getattr(api_token, 'scopes', None) or []
    return not scopes or scope in scopes


def _token_allows_platform_admin(api_token) -> bool:
    if api_token is None:
        return True
    if not token_has_scope(api_token, 'admin'):
        return False
    if getattr(api_token, 'project_id', None) is not None:
        return False
    if hasattr(api_token, 'project_scope') and api_token.project_scope() is not None:
        return False
    return True


class PlatformUpdateConsumer(AsyncWebsocketConsumer):
    """
    Real-time platform update progress and terminal log streaming consumer.
    URL: ws/platform-updates/<update_id>/?token=xxx
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.update_id = None
        self.group_name = None
        self.user = None
        self.api_token = None

    async def connect(self):
        self.update_id = self.scope['url_route']['kwargs']['update_id']

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
                self.user = await self._authenticate_token(token_key)
                self.api_token = None
            else:
                user = self.scope.get('user')
                api_token = self.scope.get('api_token')
                if user and getattr(user, 'is_authenticated', False) and getattr(user, 'is_active', False):
                    self.user = user
                    self.api_token = api_token

            if not self.user or not getattr(self.user, 'is_authenticated', False):
                logger.warning("PlatformUpdateConsumer rejected: Unauthenticated connection for update %s", self.update_id)
                await self.close(code=4001)
                return

            if not (self.user.is_staff or self.user.is_superuser) or not _token_allows_platform_admin(self.api_token):
                logger.warning("PlatformUpdateConsumer rejected: Admin access required for update %s", self.update_id)
                await self.close(code=4003)
                return

            await self.accept(subprotocol=get_websocket_subprotocol(self.scope))

            self.group_name = f"platform_update_{self.update_id}"
            await self.channel_layer.group_add(self.group_name, self.channel_name)

            update_data = await self._get_update_data()
            if update_data:
                await self.send(text_data=json.dumps({
                    "type": "initial_state",
                    "status": update_data.get("status", ""),
                    "progress_percent": update_data.get("progress_percent", 0),
                    "current_step": update_data.get("current_step", ""),
                    "logs": update_data.get("logs", ""),
                }))
                if update_data.get("logs"):
                    b64_msg = base64.b64encode(update_data["logs"].encode('utf-8')).decode('ascii')
                    await self.send(text_data=json.dumps({
                        "type": "terminal_stream",
                        "message": b64_msg,
                        "log": update_data["logs"],
                    }))
        except Exception as e:
            logger.error("Error in PlatformUpdateConsumer connect: %s", e, exc_info=True)
            with contextlib.suppress(Exception):
                await self.close(code=4000)

    async def disconnect(self, _close_code):
        if self.group_name and self.channel_layer:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def log_message(self, event):
        from apps.deployments.utils.log_scrub import mask_secrets_in_text
        log_text = mask_secrets_in_text(event.get("log", "") or "") or ""
        if log_text:
            b64_msg = base64.b64encode(log_text.encode('utf-8')).decode('ascii')
            await self.send(text_data=json.dumps({
                "type": "terminal_stream",
                "message": b64_msg,
                "log": log_text,
            }))

    async def status_message(self, event):
        from apps.deployments.utils.log_scrub import mask_secrets_in_text
        await self.send(text_data=json.dumps({
            "type": "status_change",
            "status": event.get("status"),
            "current_step": event.get("current_step"),
            "progress_percent": event.get("progress_percent"),
            "error_message": mask_secrets_in_text(event.get("error_message", "") or "") or "",
        }))

    async def _authenticate_token(self, token_key):
        user, api_token = await authenticate_ws_credential(token_key)
        if user is None:
            return None
        if not _token_allows_platform_admin(api_token):
            return None
        return user

    @database_sync_to_async
    def _get_update_data(self):
        from apps.deployments.models.updates import PlatformUpdate
        from apps.deployments.utils.log_scrub import mask_secrets_in_text
        try:
            update = PlatformUpdate.objects.get(id=self.update_id)
            return {
                "status": update.status,
                "progress_percent": update.progress_percent,
                "current_step": update.current_step,
                "logs": mask_secrets_in_text(update.logs or "") or "",
            }
        except PlatformUpdate.DoesNotExist:
            return None
