"""Frontend compatibility alias: /api/v1/preferences/

The frontend ``coreApi.getNotificationPreferences`` /
``coreApi.updateNotificationPreferences`` (api.ts:1666, 1670)
call ``GET /api/v1/preferences/`` and
``PATCH /api/v1/preferences/<id>/``. The canonical routes
are mounted at ``/api/v1/notifications/preferences/`` and
``/api/v1/notifications/preferences/<id>/``. This alias keeps
the existing frontend working without a rebuild.
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.notifications.views import NotificationPreferenceViewSet

router = DefaultRouter()
router.register(r'', NotificationPreferenceViewSet, basename='preferences')

urlpatterns = [
    path('', include(router.urls)),
]
