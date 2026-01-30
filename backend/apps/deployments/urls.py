from rest_framework.routers import DefaultRouter
from .views import ServiceViewSet, DeploymentViewSet
from .views_addons import AddonViewSet
from .views_metrics import MetricsViewSet
from .views_templates import TemplateViewSet
from .views_cron import CronJobViewSet
from .views_storage import VolumeViewSet
from .views_topology import TopologyViewSet
from .views_analysis import RepoAnalysisView
from .views_chat import AIChatView
from django.urls import path
from django.http import HttpResponse, HttpResponseForbidden
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from django.conf import settings

router = DefaultRouter()
router.register(r'services', ServiceViewSet, basename='services')
router.register(r'deployments', DeploymentViewSet, basename='deployments')
router.register(r'addons', AddonViewSet, basename='addons')
router.register(r'metrics', MetricsViewSet, basename='metrics')
router.register(r'templates', TemplateViewSet, basename='templates')
router.register(r'cronjobs', CronJobViewSet, basename='cronjobs')
router.register(r'volumes', VolumeViewSet, basename='volumes')
router.register(r'topology', TopologyViewSet, basename='topology')



# =============================================================================
# SECURITY: Prometheus metrics with IP-based restriction
# =============================================================================
PROMETHEUS_ALLOWED_IPS = getattr(settings, 'PROMETHEUS_ALLOWED_IPS', [
    '127.0.0.1',
    '::1',
    '10.0.0.0/8',  # Docker/K8s internal
    '172.16.0.0/12',  # Docker internal
    '192.168.0.0/16',  # Private network
])

def get_client_ip(request):
    """Extract client IP from request, handling proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')

def is_allowed_ip(ip):
    """Check if IP is in allowed list (simple check, not CIDR aware)."""
    import ipaddress
    try:
        client_ip = ipaddress.ip_address(ip)
        for allowed in PROMETHEUS_ALLOWED_IPS:
            if '/' in allowed:
                if client_ip in ipaddress.ip_network(allowed, strict=False):
                    return True
            elif ip == allowed:
                return True
        return False
    except ValueError:
        return False

def prometheus_metrics(request):
    """
    Expose Prometheus metrics at /metrics/prometheus.
    
    SECURITY: Restricted to internal/monitoring IPs only.
    External access should go through authenticated endpoint or be blocked at load balancer.
    """
    client_ip = get_client_ip(request)
    
    # Allow in DEBUG mode for development
    if not settings.DEBUG and not is_allowed_ip(client_ip):
        return HttpResponseForbidden(
            f"Access denied. Your IP ({client_ip}) is not in the allowed list."
        )
    
    return HttpResponse(
        generate_latest(),
        content_type=CONTENT_TYPE_LATEST
    )


urlpatterns = router.urls + [
    path('analyze-repo/', RepoAnalysisView.as_view(), name='analyze-repo'),
    path('ai/chat/', AIChatView.as_view(), name='ai-chat'),
    path('metrics/prometheus/', prometheus_metrics, name='prometheus-metrics'),
]
