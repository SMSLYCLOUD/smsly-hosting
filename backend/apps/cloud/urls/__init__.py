"""URLs for cloud app.

Naming convention: URL names use kebab-case (e.g. 'ecosystem-scan').
"""
from apps.cloud.views import (
    CloudProviderViewSet,
    CloudResourceViewSet,
    IntelligenceViewSet,
)
from apps.cloud.views.analysis import CodeIntelligenceView, DeepScanTaskStatusView
from apps.cloud.views.code_analysis import CodeAnalysisViewSet
from apps.deployments.views.github import github_repos
from django.urls import path
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'providers', CloudProviderViewSet, basename='providers')
router.register(r'resources', CloudResourceViewSet, basename='resources')
router.register(r'intelligence', IntelligenceViewSet, basename='intelligence')
router.register(r'code-analysis', CodeAnalysisViewSet, basename='code-analysis')
# Ecosystem actions are now part of IntelligenceViewSet
# router.register(r'ecosystem', EcosystemViewSet, basename='ecosystem')

urlpatterns = [
    # Backward-compatible AI assistant path used by frontend widgets.
    path(
        'intelligence/ask/',
        IntelligenceViewSet.as_view({'post': 'chat'}),
        name='intelligence-ask',
    ),
    path(
        'intelligence/ask',
        IntelligenceViewSet.as_view({'post': 'chat'}),
        name='intelligence-ask-no-slash',
    ),
    # Debug endpoint for ecosystem prompts
    path(
        'intelligence/ecosystem-prompts/',
        IntelligenceViewSet.as_view({'get': 'ecosystem_prompts'}),
        name='ecosystem-prompts',
    ),
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
        'ecosystem/add-service/',
        IntelligenceViewSet.as_view({'post': 'ecosystem_add_service'}),
        name='ecosystem-add-service',
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
    path(
        'ecosystem/cached-scan/',
        IntelligenceViewSet.as_view({'get': 'cached_scan_result'}),
        name='ecosystem-cached-scan',
    ),
    path(
        'ecosystem/active-plan/',
        IntelligenceViewSet.as_view({'get': 'active_plan'}),
        name='ecosystem-active-plan',
    ),
    path(
        'ecosystem/download-env/',
        IntelligenceViewSet.as_view({'get': 'download_env'}),
        name='ecosystem-download-env',
    ),
    path(
        'ecosystem/plans/',
        IntelligenceViewSet.as_view({'get': 'list_plans'}),
        name='ecosystem-plans-list',
    ),
    path(
        'ecosystem/plans/<uuid:plan_id>/',
        IntelligenceViewSet.as_view({'get': 'plan_detail'}),
        name='ecosystem-plan-detail',
    ),
    path(
        'ecosystem/plans/<uuid:plan_id>/restore-snapshots/',
        IntelligenceViewSet.as_view({'post': 'restore_snapshots'}),
        name='ecosystem-plan-restore-snapshots',
    ),
    path(
        'ecosystem/deep_scan/',
        CodeIntelligenceView.as_view(),
        name='cloud-ecosystem-deep-scan',
    ),
    path(
        'ecosystem/deep_scan/status/',
        DeepScanTaskStatusView.as_view(),
        name='cloud-ecosystem-deep-scan-status',
    ),
    # Backward-compatible github integrations path for cached frontends.
    path(
        'integrations/github/repos/',
        github_repos,
        name='cloud-github-repos-alias',
    ),
    *router.urls,
]
