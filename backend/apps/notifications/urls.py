from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, NotificationPreferenceViewSet

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'preferences', NotificationPreferenceViewSet, basename='preferences')

urlpatterns = [
    path('', include(router.urls)),
]
