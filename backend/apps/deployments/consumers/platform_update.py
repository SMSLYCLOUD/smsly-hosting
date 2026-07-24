"""Real-time platform update progress and terminal log streaming consumer."""
import base64
import contextlib
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .base import authenticate_ws_token, get_websocket_subprotocol, logger


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

            if not token_key:
                query_string = self.scope.get('query_string', b'').decode()
                for param in query_string.split('&'):
                    if param.startswith('token='):
                        token_key = param.split('=', 1)[1]
                        break

            if token_key:
                self.user = await self._authenticate_token(token_key)
            else:
                user = self.scope.get('user')
                if user and getattr(user, 'is_authenticated', False) and getattr(user, 'is_active', False):
                    self.user = user

            if not self.user or not getattr(self.user, 'is_authenticated', False):
                logger.warning("PlatformUpdateConsumer rejected: Unauthenticated connection for update %s", self.update_id)
                await self.close(code=4001)
                return

            if not (self.user.is_staff or self.user.is_superuser):
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
        log_text = event.get("log", "")
        if log_text:
            b64_msg = base64.b64encode(log_text.encode('utf-8')).decode('ascii')
            await self.send(text_data=json.dumps({
                "type": "terminal_stream",
                "message": b64_msg,
                "log": log_text,
            }))

    async def status_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "status_change",
            "status": event.get("status"),
            "current_step": event.get("current_step"),
            "progress_percent": event.get("progress_percent"),
            "error_message": event.get("error_message", ""),
        }))

    async def _authenticate_token(self, token_key):
        return await authenticate_ws_token(token_key)

    @database_sync_to_async
    def _get_update_data(self):
        from .models.updates import PlatformUpdate
        try:
            update = PlatformUpdate.objects.get(id=self.update_id)
            return {
                "status": update.status,
                "progress_percent": update.progress_percent,
                "current_step": update.current_step,
                "logs": update.logs,
            }
        except PlatformUpdate.DoesNotExist:
            return None
