"""
ASGI config for smsly_hosting project.
"""

import os

import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import apps.deployments.routing  # noqa: E402
from apps.deployments.middleware import (  # noqa: E402
    QueryStringAuthMiddleware,
    RedisResilientMiddleware,
    DynamicAllowedHostsASGIMiddleware,
)
from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

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
