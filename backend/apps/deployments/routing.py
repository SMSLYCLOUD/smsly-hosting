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
]
