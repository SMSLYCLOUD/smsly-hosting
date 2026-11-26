"""
ASGI config for smsly_hosting project.
"""

import os

import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import apps.deployments.routing
from apps.core.middleware.dynamic_hosts import (
    ControlPlaneOriginValidator,
    DynamicAllowedHostsASGIMiddleware,
    QueryStringAuthMiddleware,
    RedisResilientMiddleware,
    SameOriginCookieWebsocketValidator,
)
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": DynamicAllowedHostsASGIMiddleware(
        RedisResilientMiddleware(
            ControlPlaneOriginValidator(
                AuthMiddlewareStack(
                    QueryStringAuthMiddleware(
                        SameOriginCookieWebsocketValidator(
                            URLRouter(
                                apps.deployments.routing.websocket_urlpatterns
                            )
                        )
                    )
                )
            )
        )
    ),
})
