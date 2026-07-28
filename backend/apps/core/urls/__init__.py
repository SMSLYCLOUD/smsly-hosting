"""Core app URL patterns.

Naming convention: URL names use kebab-case (e.g. 'dashboard-overview').
"""
from apps.core.views import (
    AdminUserViewSet,
    APIKeyViewSet,
    ContactView,
    DashboardOverviewView,
    SubdomainStubViewSet,
    SystemResourcesView,
)
from apps.core.views.observability import (
    grafana_embed_url,
    loki_label_values,
    loki_query,
    prometheus_query,
)
from django.urls import include, path
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'api-keys', APIKeyViewSet, basename='api-keys')
router.register(r'subdomains', SubdomainStubViewSet, basename='subdomains')
router.register(r'admin/users', AdminUserViewSet, basename='admin-users')

urlpatterns = [
    path('contact/', ContactView.as_view(), name='contact'),
    path('dashboard/overview/', DashboardOverviewView.as_view(), name='dashboard-overview'),
    path('system/resources/', SystemResourcesView.as_view(), name='system-resources'),
    path('observability/grafana/embed/<str:dashboard_uid>/', grafana_embed_url, name='observability-grafana-embed'),
    path('observability/loki/query/', loki_query, name='observability-loki-query'),
    path('observability/loki/label/<str:label>/values/', loki_label_values, name='observability-loki-label-values'),
    path('observability/prometheus/query/', prometheus_query, name='observability-prometheus-query'),
    path('', include(router.urls)),
]
