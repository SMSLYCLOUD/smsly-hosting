"""Routing module."""
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(r'ws/terminal/(?P<deployment_id>\w+)/$',
            consumers.TerminalConsumer.as_asgi()),
    re_path(r'ws/build-logs/(?P<deployment_id>\w+)/$',
            consumers.BuildLogConsumer.as_asgi()),
]
