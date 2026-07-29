"""Frontend compatibility alias: /api/v1/api-keys/

The frontend ``coreApi.getApiKeys / createApiKey / revokeApiKey``
(lib/api.ts:1636, 1640, 1644) call GET/POST/DELETE on
``/api/v1/api-keys/``. The canonical route is mounted at
``/api/v1/core/api-keys/`` (r'api-keys' router under core app).
This alias keeps the existing frontend working without a
rebuild.
"""
from apps.core.views import APIKeyViewSet
from django.urls import include, path
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'', APIKeyViewSet, basename='api-keys-alias')

urlpatterns = [
    path('', include(router.urls)),
]
