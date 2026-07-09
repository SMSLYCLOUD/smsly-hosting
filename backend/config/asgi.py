"""
ASGI config for smsly_hosting project.
"""

import os

import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import apps.deployments.routing
from apps.deployments.middleware import (
    DynamicAllowedHostsASGIMiddleware,
    QueryStringAuthMiddleware,
    RedisResilientMiddleware,
)
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": DynamicAllowedHostsASGIMiddleware(
        RedisResilientMiddleware(
            AllowedHostsOriginValidator(
                AuthMiddlewareStack(
                    QueryStringAuthMiddleware(
                        URLRouter(
                            apps.deployments.routing.websocket_urlpatterns
                        )
                    )
                )
            )
        )
    ),
})
