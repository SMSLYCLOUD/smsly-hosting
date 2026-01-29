from rest_framework.routers import DefaultRouter
from .views import ServiceViewSet, DeploymentViewSet
from .views_addons import AddonViewSet
from .views_metrics import MetricsViewSet
from .views_templates import TemplateViewSet
from .views_cron import CronJobViewSet
from .views_storage import VolumeViewSet
from .views_topology import TopologyViewSet
from .views_analysis import RepoAnalysisView
from django.urls import path
from django.http import HttpResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

router = DefaultRouter()
router.register(r'services', ServiceViewSet)
router.register(r'deployments', DeploymentViewSet)
router.register(r'addons', AddonViewSet)
router.register(r'metrics', MetricsViewSet, basename='metrics')
router.register(r'templates', TemplateViewSet)
router.register(r'cronjobs', CronJobViewSet, basename='cronjobs')
router.register(r'volumes', VolumeViewSet, basename='volumes')
router.register(r'topology', TopologyViewSet, basename='topology')


def prometheus_metrics(request):
    """Expose Prometheus metrics at /metrics/prometheus."""
    return HttpResponse(
        generate_latest(),
        content_type=CONTENT_TYPE_LATEST
    )


urlpatterns = router.urls + [
    path('analyze-repo/', RepoAnalysisView.as_view(), name='analyze-repo'),
    path('metrics/prometheus/', prometheus_metrics, name='prometheus-metrics'),
]
