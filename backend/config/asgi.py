"""
ASGI config for smsly_hosting project.
"""

import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator
from apps.deployments.middleware import QueryStringAuthMiddleware
import apps.deployments.routing

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            QueryStringAuthMiddleware(
                URLRouter(
                    apps.deployments.routing.websocket_urlpatterns
                )
            )
        )
    ),
})
