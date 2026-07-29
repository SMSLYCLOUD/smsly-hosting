"""Frontend compatibility alias: /api/v1/resource-alerts/

The frontend ``ResourceAlerts`` component (ResourceAlerts.tsx:33)
calls ``GET /api/v1/resource-alerts/?service=<id>``. The
canonical route is mounted at
``/api/v1/notifications/resource-alerts/``. This alias
keeps the existing frontend working without a rebuild.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.notifications.views import ResourceAlertViewSet

router = DefaultRouter()
router.register(r'', ResourceAlertViewSet, basename='resource-alerts-alias')

urlpatterns = [
    path('', include(router.urls)),
]
