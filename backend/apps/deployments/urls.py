from rest_framework.routers import DefaultRouter
from django.urls import path
from .views import DeploymentViewSet, ServiceViewSet
from .views_addons import AddonViewSet
from .views_metrics import MetricsViewSet
from .views_templates import TemplateViewSet
from .views_storage import VolumeViewSet
from .views_cron import CronJobViewSet
from .views_topology import TopologyViewSet
from .views_blueprints import BlueprintViewSet
from .views_webhooks import GitHubWebhookView

router = DefaultRouter()

# Legacy Endpoints
router.register(r'services', ServiceViewSet, basename='services')
router.register(r'addons', AddonViewSet, basename='addons')
router.register(r'metrics', MetricsViewSet, basename='metrics')
router.register(r'templates', TemplateViewSet, basename='templates')
router.register(r'volumes', VolumeViewSet, basename='volumes')
router.register(r'cron', CronJobViewSet, basename='cron')
router.register(r'topology', TopologyViewSet, basename='topology')

# New Endpoints
router.register(r'deployments', DeploymentViewSet, basename='deployments')
router.register(r'blueprints', BlueprintViewSet, basename='blueprints')

urlpatterns = router.urls + [
    path('integrations/github/', GitHubWebhookView.as_view(), name='github-webhook'),
]
