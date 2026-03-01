"""URLs for cloud app."""
from django.urls import path
from rest_framework.routers import DefaultRouter
from apps.cloud.views import CloudProviderViewSet, CloudResourceViewSet, IntelligenceViewSet
from apps.cloud.views_code_analysis import CodeAnalysisViewSet

router = DefaultRouter()
router.register(r'providers', CloudProviderViewSet, basename='providers')
router.register(r'resources', CloudResourceViewSet, basename='resources')
router.register(r'intelligence', IntelligenceViewSet, basename='intelligence')
router.register(r'code-analysis', CodeAnalysisViewSet, basename='code-analysis')
# Ecosystem actions are now part of IntelligenceViewSet
# router.register(r'ecosystem', EcosystemViewSet, basename='ecosystem')

urlpatterns = [
    *router.urls,
    # Backward-compatible ecosystem paths used by the frontend.
    path(
        'ecosystem/scan/',
        IntelligenceViewSet.as_view({'post': 'ecosystem_scan'}),
        name='ecosystem-scan',
    ),
    path(
        'ecosystem/deploy/',
        IntelligenceViewSet.as_view({'post': 'ecosystem_deploy'}),
        name='ecosystem-deploy',
    ),
    path(
        'ecosystem/task_status/',
        IntelligenceViewSet.as_view({'get': 'task_status'}),
        name='ecosystem-task-status',
    ),
    path(
        'ecosystem/bulk-env/',
        IntelligenceViewSet.as_view({'post': 'ecosystem_bulk_env'}),
        name='ecosystem-bulk-env',
    ),
]
