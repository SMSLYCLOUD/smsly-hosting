from rest_framework.routers import DefaultRouter
from django.urls import path, include
from rest_framework_nested import routers
from .views import DeploymentViewSet, ServiceViewSet
from .views_addons import AddonViewSet
from .views_metrics import MetricsViewSet
from .views_cron import CronJobViewSet
from .views_storage import VolumeViewSet

# Create main router
router = DefaultRouter()
router.register(r'services', ServiceViewSet, basename='services')
router.register(r'deployments', DeploymentViewSet, basename='deployments')
router.register(r'addons', AddonViewSet, basename='addons')

# Nested Router
# /api/v1/services/{service_pk}/metrics/
# /api/v1/services/{service_pk}/cron/
# /api/v1/services/{service_pk}/volumes/
services_router = routers.NestedSimpleRouter(router, r'services', lookup='service')
services_router.register(r'metrics', MetricsViewSet, basename='service-metrics')
services_router.register(r'cron', CronJobViewSet, basename='service-cron')
services_router.register(r'volumes', VolumeViewSet, basename='service-volumes')

urlpatterns = router.urls + [
    path('', include(services_router.urls)),
]
