"""Routing module."""
from django.urls import re_path

from channels.generic.websocket import AsyncWebsocketConsumer

from . import consumers
from apps.addons.consumers.addon_terminal import AddonTerminalConsumer


class UnknownWsConsumer(AsyncWebsocketConsumer):
    """Fail-closed catch-all for unknown WebSocket paths.

    Channels' URLRouter raises ``ValueError`` (an unclean drop that
    surfaces as a 500 in the ASGI server logs) when no route matches.
    This trailing catch-all matches everything left over and closes
    cleanly with 4404 so unknown paths never 500.
    """

    async def connect(self):
        await self.close(code=4404)

    async def receive(self, text_data=None, bytes_data=None):
        await self.close(code=4404)

    async def disconnect(self, code):
        return None

websocket_urlpatterns = [
    # NOTE: Deployment IDs are UUIDs (include hyphens), so we must accept `-`.
    re_path(r'ws/terminal/(?P<deployment_id>[-\w]+)/$',
            consumers.TerminalConsumer.as_asgi()),
    re_path(r'ws/addon-terminal/(?P<addon_id>[-\w]+)/$',
            AddonTerminalConsumer.as_asgi()),
    re_path(r'ws/build-logs/(?P<deployment_id>[-\w]+)/$',
            consumers.BuildLogConsumer.as_asgi()),
    re_path(r'ws/runtime-logs/(?P<deployment_id>[-\w]+)/$',
            consumers.RuntimeLogConsumer.as_asgi()),
    re_path(r'ws/service-status/$',
            consumers.ServiceStatusConsumer.as_asgi()),
    re_path(r'ws/addon-logs/(?P<addon_id>[-\w]+)/$',
            consumers.AddonLogConsumer.as_asgi()),    re_path(r'ws/backup-progress/(?P<backup_id>[-\w]+)/$',
            consumers.BackupProgressConsumer.as_asgi()),
    re_path(r'ws/platform-updates/(?P<update_id>[-\w]+)/$',
            consumers.PlatformUpdateConsumer.as_asgi()),
    # Also support paths with /api/v1/ prefix for compatibility
    re_path(r'api/v1/ws/terminal/(?P<deployment_id>[-\w]+)/$',
            consumers.TerminalConsumer.as_asgi()),
    re_path(r'api/v1/ws/addon-terminal/(?P<addon_id>[-\w]+)/$',
            AddonTerminalConsumer.as_asgi()),
    re_path(r'api/v1/ws/build-logs/(?P<deployment_id>[-\w]+)/$',
            consumers.BuildLogConsumer.as_asgi()),
    re_path(r'api/v1/ws/runtime-logs/(?P<deployment_id>[-\w]+)/$',
            consumers.RuntimeLogConsumer.as_asgi()),
    re_path(r'api/v1/ws/service-status/$',
            consumers.ServiceStatusConsumer.as_asgi()),
    re_path(r'api/v1/ws/addon-logs/(?P<addon_id>[-\w]+)/$',
            consumers.AddonLogConsumer.as_asgi()),
    re_path(r'api/v1/ws/backup-progress/(?P<backup_id>[-\w]+)/$',
            consumers.BackupProgressConsumer.as_asgi()),
    re_path(r'api/v1/ws/platform-updates/(?P<update_id>[-\w]+)/$',
            consumers.PlatformUpdateConsumer.as_asgi()),
    # Fail-closed catch-all: unknown WS paths close cleanly with 4404
    # instead of raising ValueError (unclean drop / ASGI 500). These
    # MUST stay last — routes are tried in order.
    re_path(r'ws/.*$',
            UnknownWsConsumer.as_asgi()),
    re_path(r'api/v1/ws/.*$',
            UnknownWsConsumer.as_asgi()),
    re_path(r'.*$',
            UnknownWsConsumer.as_asgi()),
]
