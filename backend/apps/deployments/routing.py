"""Routing module."""
from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    # NOTE: Deployment IDs are UUIDs (include hyphens), so we must accept `-`.
    re_path(r'ws/terminal/(?P<deployment_id>[-\w]+)/$',
            consumers.TerminalConsumer.as_asgi()),
    re_path(r'ws/build-logs/(?P<deployment_id>[-\w]+)/$',
            consumers.BuildLogConsumer.as_asgi()),
    re_path(r'ws/service-status/$',
            consumers.ServiceStatusConsumer.as_asgi()),
    re_path(r'ws/addon-logs/(?P<addon_id>[-\w]+)/$',
            consumers.AddonLogConsumer.as_asgi()),
    re_path(r'ws/backup-progress/(?P<backup_id>[-\w]+)/$',
            consumers.BackupProgressConsumer.as_asgi()),
    re_path(r'ws/platform-updates/(?P<update_id>[-\w]+)/$',
            consumers.PlatformUpdateConsumer.as_asgi()),
    # Also support paths with /api/v1/ prefix for compatibility
    re_path(r'api/v1/ws/terminal/(?P<deployment_id>[-\w]+)/$',
            consumers.TerminalConsumer.as_asgi()),
    re_path(r'api/v1/ws/build-logs/(?P<deployment_id>[-\w]+)/$',
            consumers.BuildLogConsumer.as_asgi()),
    re_path(r'api/v1/ws/service-status/$',
            consumers.ServiceStatusConsumer.as_asgi()),
    re_path(r'api/v1/ws/addon-logs/(?P<addon_id>[-\w]+)/$',
            consumers.AddonLogConsumer.as_asgi()),
    re_path(r'api/v1/ws/backup-progress/(?P<backup_id>[-\w]+)/$',
            consumers.BackupProgressConsumer.as_asgi()),
    re_path(r'api/v1/ws/platform-updates/(?P<update_id>[-\w]+)/$',
            consumers.PlatformUpdateConsumer.as_asgi()),
]
