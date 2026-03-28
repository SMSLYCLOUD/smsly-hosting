"""
ASGI config for smsly_hosting project.
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator
from apps.deployments.middleware import QueryStringAuthMiddleware
import apps.deployments.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        QueryStringAuthMiddleware(
            AuthMiddlewareStack(
                URLRouter(
                    apps.deployments.routing.websocket_urlpatterns
                )
            )
        )
    ),
})
