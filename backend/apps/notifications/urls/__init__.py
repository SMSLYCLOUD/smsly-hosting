from django.urls import include, path
from rest_framework.routers import DefaultRouter

from ..views import (
    AlertRuleViewSet,
    NotificationChannelViewSet,
    NotificationPreferenceViewSet,
    NotificationViewSet,
    ResourceAlertViewSet,
    test_smtp,
)

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'preferences', NotificationPreferenceViewSet, basename='preferences')
router.register(r'resource-alerts', ResourceAlertViewSet, basename='resource-alerts')
router.register(r'channels', NotificationChannelViewSet, basename='notification-channel')
router.register(r'rules', AlertRuleViewSet, basename='alert-rule')

urlpatterns = [
    path('', include(router.urls)),
    path('test-smtp/', test_smtp, name='test-smtp'),
]
