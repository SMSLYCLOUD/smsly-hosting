from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    NotificationPreferenceViewSet,
    NotificationViewSet,
    ResourceAlertViewSet,
)

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'preferences', NotificationPreferenceViewSet, basename='preferences')
router.register(r'resource-alerts', ResourceAlertViewSet, basename='resource-alerts')

urlpatterns = [
    path('', include(router.urls)),
]
