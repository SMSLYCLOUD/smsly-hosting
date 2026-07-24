from django.urls import include, path
from rest_framework.routers import DefaultRouter

from ..views import AddonMaintenanceViewSet

router = DefaultRouter()
router.register(r'maintenance', AddonMaintenanceViewSet, basename='addon-maintenance')

urlpatterns = [
    path('', include(router.urls)),
]
