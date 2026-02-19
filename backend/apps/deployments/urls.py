"""Urls module."""
from rest_framework.routers import DefaultRouter
from django.urls import path, include
from rest_framework_nested import routers
from .views import (
    DeploymentViewSet, ServiceViewSet, SessionTokenView, SystemConfigView, AuditLogViewSet, DomainConfigView,
    ServiceBackupViewSet, ServerBackupViewSet, BackupScheduleViewSet
)
from .views_transfer import ServerTransferViewSet
from .views_addons import AddonViewSet
from .views_metrics import MetricsViewSet
from .views_cron import CronJobViewSet
from .views_storage import VolumeViewSet
from .views_templates import TemplateViewSet
from .views_blueprints import BlueprintViewSet
from .views_topology import TopologyViewSet
from .views_analysis import RepoAnalysisView
from .views_chat import AIChatView
from .views_webhooks import GitHubWebhookView
from .views_tunnels import TunnelViewSet
from .views_oauth import oauth_providers_status, oauth_credentials
from .views_integrations import github_connection
from .views_github import github_repos
from .views_tokens import list_tokens, create_token, revoke_token
from .views_servers import ManagedServerViewSet

# Create main router
router = DefaultRouter()
# CHANGED: basename='service' to match convention used in tests (or update tests)
# Tests expect 'service-list' and 'deployment-list'.
# DRF DefaultRouter with basename='service' creates 'service-list', 'service-detail'.
# Current code has basename='services', creating 'services-list'.
router.register(r'services', ServiceViewSet, basename='service')
router.register(r'deployments', DeploymentViewSet, basename='deployment')
router.register(r'addons', AddonViewSet, basename='addon')
router.register(r'blueprints', BlueprintViewSet, basename='blueprint')
router.register(r'topology', TopologyViewSet, basename='topology')
router.register(r'tunnels', TunnelViewSet, basename='tunnel')
router.register(r'audit-logs', AuditLogViewSet, basename='auditlog')
router.register(r'servers', ManagedServerViewSet, basename='server')
router.register(r'backups', ServiceBackupViewSet, basename='backup')
router.register(r'server/backups', ServerBackupViewSet, basename='server-backup')
router.register(r'backup-schedules', BackupScheduleViewSet, basename='backup-schedule')
router.register(r'transfers', ServerTransferViewSet, basename='transfer')

# Nested Router
# /api/v1/services/{service_pk}/metrics/
# /api/v1/services/{service_pk}/cron/
# /api/v1/services/{service_pk}/volumes/
services_router = routers.NestedSimpleRouter(
    router, r'services', lookup='service')
services_router.register(
    r'metrics',
    MetricsViewSet,
    basename='service-metrics')
services_router.register(r'cron', CronJobViewSet, basename='service-cron')
services_router.register(r'volumes', VolumeViewSet, basename='service-volumes')
services_router.register(r'backups', ServiceBackupViewSet, basename='service-backup')

urlpatterns = router.urls + [
    path('templates/', TemplateViewSet.as_view({'get': 'list'}), name='template-list'),
    path('templates/<str:pk>/', TemplateViewSet.as_view({'get': 'retrieve'}), name='template-detail'),
    path(
        'templates/<str:pk>/one_click_deploy/',
        TemplateViewSet.as_view({'post': 'one_click_deploy'}),
        name='template-one-click-deploy',
    ),
    path('', include(services_router.urls)),
    # Non-router views
    path('auth/session-token/', SessionTokenView.as_view(), name='session-token'),
    path('analyze-repo/', RepoAnalysisView.as_view(), name='analyze-repo'),
    path('ai-chat/', AIChatView.as_view(), name='ai-chat'),
    path('webhooks/github/', GitHubWebhookView.as_view(), name='github-webhook'),
    path('system/config/', SystemConfigView.as_view(), name='system-config'),
    path('system/domain-config/', DomainConfigView.as_view(), name='domain-config'),
    path('oauth/status/', oauth_providers_status, name='oauth-status'),
    path('oauth/credentials/', oauth_credentials, name='oauth-credentials'),
    path('integrations/github/', github_connection, name='github-connection'),
    path('integrations/github/repos/', github_repos, name='github-repos'),
    # API Token management (for CLI)
    path('tokens/', list_tokens, name='token-list'),
    path('tokens/create/', create_token, name='token-create'),
    path('tokens/<uuid:token_id>/revoke/', revoke_token, name='token-revoke'),
]
