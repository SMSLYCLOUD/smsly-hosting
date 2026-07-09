"""Urls module."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_nested import routers

from .views import (
    AuditLogViewSet,
    BackupScheduleViewSet,
    DeploymentViewSet,
    DomainConfigView,
    PlatformConfigViewSet,
    PlatformResourcesView,
    RegistryCredentialViewSet,
    RemoteTriggerView,
    RouteRecheckView,
    SecurityStatusView,
    ServerBackupViewSet,
    ServiceBackupViewSet,
    ServiceSnapshotViewSet,
    ServiceViewSet,
    SessionTokenView,
    SnapshotScheduleViewSet,
    SystemConfigView,
)
from .views_addons import AddonViewSet, service_addons_unified
from .views_analysis import RepoAnalysisView
from .views_autoscale import ScalingViewSet
from .views_bitbucket import bitbucket_branches, bitbucket_commits, bitbucket_repos
from .views_blueprints import BlueprintViewSet
from .views_bundles import BundleViewSet
from .views_chat import AIChatView
from .views_cloud_storage import CloudStorageViewSet
from .views_cron import CronJobViewSet
from .views_database_replica import DatabaseReplicaViewSet
from .views_device import list_devices, register_device, revoke_device
from .views_election import ClusterViewSet, heartbeat_receive, vote_request
from .views_github import github_branches, github_commits, github_repos
from .views_gitlab import gitlab_branches, gitlab_commits, gitlab_repos
from .views_health_webhook import ServiceHealthWebhookView
from .views_integrations import (
    bitbucket_connection,
    bitbucket_oauth_callback,
    bitbucket_oauth_url,
    github_connect,
    github_connection,
    github_oauth_callback,
    github_oauth_url,
    gitlab_connection,
    gitlab_oauth_callback,
    gitlab_oauth_url,
)
from .views_mesh import MeshNetworkViewSet
from .views_metrics import MetricsViewSet
from .views_network_scope import ScopedNetworkViewSet
from .views_node_exchange import node_token_exchange, node_token_exchange_via_gateway
from .views_oauth import oauth_credentials, oauth_providers_status
from .views_project import ProjectViewSet
from .views_recovery import recovery_phrase_generate, recovery_phrase_verify
from .views_registry_auth import registry_token
from .views_registry_scope import ScopedRegistryViewSet
from .views_replication import ReplicationViewSet
from .views_safedeploy import DeploymentApprovalViewSet, PreviewEnvironmentViewSet
from .views_servers import ManagedServerViewSet
from .views_slow_query import SlowQueryViewSet
from .views_storage import VolumeViewSet
from .views_subdomains import subdomains_list_create, subdomains_release
from .views_templates import TemplateViewSet
from .views_tokens import create_token, list_tokens, revoke_token
from .views_topology import TopologyViewSet
from .views_traffic import TrafficGeoViewSet
from .views_transfer import ServerTransferViewSet
from .views_tunnels import TunnelViewSet
from .views_updates import PlatformUpdateViewSet
from .views_webhooks import BitbucketWebhookView, GitHubWebhookView, GitLabWebhookView

# Create main router
router = DefaultRouter()
# CHANGED: basename='service' to match convention used in tests (or update tests)
# Tests expect 'service-list' and 'deployment-list'.
# DRF DefaultRouter with basename='service' creates 'service-list', 'service-detail'.
# Current code has basename='services', creating 'services-list'.
router.register(r'services', ServiceViewSet, basename='service')
router.register(r'deployments', DeploymentViewSet, basename='deployment')
router.register(r'addons', AddonViewSet, basename='addon')
router.register(r'bundles', BundleViewSet, basename='bundle')
router.register(r'blueprints', BlueprintViewSet, basename='blueprint')
router.register(r'topology', TopologyViewSet, basename='topology')
router.register(r'tunnels', TunnelViewSet, basename='tunnel')
router.register(r'audit-logs', AuditLogViewSet, basename='auditlog')
router.register(r'servers', ManagedServerViewSet, basename='server')
router.register(r'backups', ServiceBackupViewSet, basename='backup')
router.register(r'snapshots', ServiceSnapshotViewSet, basename='snapshot')
router.register(r'server/backups', ServerBackupViewSet, basename='server-backup')
router.register(r'backup-schedules', BackupScheduleViewSet, basename='backup-schedule')
router.register(r'snapshot-schedules', SnapshotScheduleViewSet, basename='snapshot-schedule')
router.register(r'transfers', ServerTransferViewSet, basename='transfer')
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'mesh', MeshNetworkViewSet, basename='mesh')
router.register(r'clusters', ClusterViewSet, basename='cluster')
router.register(r'replication', ReplicationViewSet, basename='replication')
router.register(r'platform-updates', PlatformUpdateViewSet, basename='platform-update')
router.register(r'scaling', ScalingViewSet, basename='scaling')
router.register(r'cloud-storage', CloudStorageViewSet, basename='cloud-storage')
router.register(r'slow-queries', SlowQueryViewSet, basename='slow-query')
router.register(r'database-replicas', DatabaseReplicaViewSet, basename='database-replica')
router.register(r'registry-credentials', RegistryCredentialViewSet, basename='registry-credential')
router.register(r'registry-scopes', ScopedRegistryViewSet, basename='registry-scope')
router.register(r'network-scopes', ScopedNetworkViewSet, basename='network-scope')
router.register(r'platform-config', PlatformConfigViewSet, basename='platform-config')

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
services_router.register(r'snapshots', ServiceSnapshotViewSet, basename='service-snapshot')
services_router.register(r'previews', PreviewEnvironmentViewSet, basename='service-previews')
services_router.register(r'approvals', DeploymentApprovalViewSet, basename='service-approvals')
services_router.register(r'traffic-geo', TrafficGeoViewSet, basename='service-traffic-geo')

# ── CRITICAL: Explicit Addon Actions (must be before router.urls to avoid 404 shadowing)
urlpatterns = [
    path('deployments/trigger/', DeploymentViewSet.as_view({'post': 'trigger'}), name='deployment-trigger'),
    path('deployments/upload/', DeploymentViewSet.as_view({'post': 'upload_source'}), name='deployment-upload'),
    path('addons/<uuid:pk>/toggle_bucket_public/', AddonViewSet.as_view({'post': 'toggle_bucket_public'}), name='addon-toggle-bucket-public-direct'),
    path('addons/<uuid:pk>/deprovision/', AddonViewSet.as_view({'post': 'deprovision'}), name='addon-deprovision-direct'),
    path('bundles/<uuid:pk>/deprovision/', BundleViewSet.as_view({'post': 'deprovision'}), name='bundle-deprovision-direct'),
    path('bundles/<uuid:pk>/reprovision/', BundleViewSet.as_view({'post': 'reprovision'}), name='bundle-reprovision-direct'),
    # Unified addons + bundles endpoint for the Addons tab
    path('services/<uuid:service_id>/addons-all/', service_addons_unified, name='service-addons-unified'),
    path('services/check-domain/', ServiceViewSet.as_view({'get': 'check_domain'}), name='service-check-domain-direct'),
    path('services/check-domain', ServiceViewSet.as_view({'get': 'check_domain'}), name='service-check-domain-direct-noslash'),
    path('topology/ecosystem/', TopologyViewSet.as_view({'get': 'ecosystem'}), name='topology-ecosystem'),
    path('topology/ecosystem', TopologyViewSet.as_view({'get': 'ecosystem'}), name='topology-ecosystem-noslash'),
] + router.urls + [

    path('templates/', TemplateViewSet.as_view({'get': 'list'}), name='template-list'),
    path('templates/<str:pk>/', TemplateViewSet.as_view({'get': 'retrieve'}), name='template-detail'),
    path(
        'templates/<str:pk>/one_click_deploy/',
        TemplateViewSet.as_view({'post': 'one_click_deploy'}),
        name='template-one-click-deploy',
    ),
    path('', include(services_router.urls)),
    # Backward-compatible aliases (hyphen style) for env vars endpoints.
    path(
        'services/<uuid:pk>/env-vars/',
        ServiceViewSet.as_view({'get': 'env_vars', 'post': 'env_vars'}),
        name='service-env-vars-hyphen',
    ),
    path(
        'services/<uuid:pk>/env-vars/<int:var_id>/',
        ServiceViewSet.as_view({'get': 'env_var_detail', 'delete': 'env_var_detail', 'patch': 'env_var_detail'}),
        name='service-env-var-detail-hyphen',
    ),
    # Non-router views
    path('auth/session-token/', SessionTokenView.as_view(), name='session-token'),
    path('analyze-repo/', RepoAnalysisView.as_view(), name='analyze-repo'),
    path('ai-chat/', AIChatView.as_view(), name='ai-chat'),
    path('webhooks/github/', GitHubWebhookView.as_view(), name='github-webhook'),
    path('webhooks/gitlab/', GitLabWebhookView.as_view(), name='gitlab-webhook'),
    path('webhooks/bitbucket/', BitbucketWebhookView.as_view(), name='bitbucket-webhook'),
    path('services/<uuid:service_id>/health/webhook/', ServiceHealthWebhookView.as_view(), name='service-health-webhook'),
    path('system/config/', SystemConfigView.as_view(), name='system-config'),
    path('system/security-status/', SecurityStatusView.as_view(), name='security-status'),
    path('system/domain-config/', DomainConfigView.as_view(), name='domain-config'),
    path('system/route-recheck/', RouteRecheckView.as_view(), name='route-recheck'),
    path('platform/resources/', PlatformResourcesView.as_view(), name='platform-resources'),
    path('oauth/status/', oauth_providers_status, name='oauth-status'),
    path('oauth/credentials/', oauth_credentials, name='oauth-credentials'),
    path('integrations/github/', github_connection, name='github-connection'),
    path('integrations/github/connect/', github_connect, name='github-connect'),
    path('integrations/github/repos/', github_repos, name='github-repos'),
    path('integrations/github/branches/', github_branches, name='github-branches'),
    path('integrations/github/commits/', github_commits, name='github-commits'),
    # API-based OAuth (bypasses session cookies for SPA compatibility)
    path('integrations/github/oauth-url/', github_oauth_url, name='github-oauth-url'),
    path('integrations/github/oauth-callback/', github_oauth_callback, name='github-oauth-callback'),
    # GitLab integration
    path('integrations/gitlab/', gitlab_connection, name='gitlab-connection'),
    path('integrations/gitlab/oauth-url/', gitlab_oauth_url, name='gitlab-oauth-url'),
    path('integrations/gitlab/oauth-callback/', gitlab_oauth_callback, name='gitlab-oauth-callback'),
    # Bitbucket integration
    path('integrations/bitbucket/', bitbucket_connection, name='bitbucket-connection'),
    path('integrations/bitbucket/oauth-url/', bitbucket_oauth_url, name='bitbucket-oauth-url'),
    path('integrations/bitbucket/oauth-callback/', bitbucket_oauth_callback, name='bitbucket-oauth-callback'),
    # GitLab repos
    path('integrations/gitlab/repos/', gitlab_repos, name='gitlab-repos'),
    path('integrations/gitlab/branches/', gitlab_branches, name='gitlab-branches'),
    path('integrations/gitlab/commits/', gitlab_commits, name='gitlab-commits'),
    # Bitbucket repos
    path('integrations/bitbucket/repos/', bitbucket_repos, name='bitbucket-repos'),
    path('integrations/bitbucket/branches/', bitbucket_branches, name='bitbucket-branches'),
    path('integrations/bitbucket/commits/', bitbucket_commits, name='bitbucket-commits'),
    # API Token management (for CLI)
    path('tokens/', list_tokens, name='token-list'),
    path('tokens/create/', create_token, name='token-create'),
    path('tokens/<uuid:token_id>/revoke/', revoke_token, name='token-revoke'),
    # Subdomain reservation
    path('subdomains/', subdomains_list_create, name='subdomains-list-create'),
    path('subdomains/<str:subdomain>/', subdomains_release, name='subdomains-release'),
    # Internal endpoints (WireGuard mesh, no auth)
    path('internal/heartbeat/', heartbeat_receive, name='internal-heartbeat'),
    path('internal/vote/', vote_request, name='internal-vote'),
    # Node-to-node auto token exchange
    path('auth/node-token-exchange/', node_token_exchange, name='node-token-exchange'),
    path('auth/node-token-exchange-hmac/', node_token_exchange_via_gateway, name='node-token-exchange-hmac'),
    # Agent self-registration (HMAC auth via gateway_secret, no user session).
    # Explicit URLs (not just router actions) so the views run without
    # IsAuthenticated/IsAdminUser. The viewset's `agent_ready` and
    # `agent_heartbeat` actions authenticate via the per-server
    # gateway_secret (see services/agent_registrar_auth.py).
    path(
        'servers/<uuid:pk>/agent-ready/',
        ManagedServerViewSet.as_view({'post': 'agent_ready'}),
        name='server-agent-ready',
    ),
    path(
        'servers/<uuid:pk>/agent-heartbeat/',
        ManagedServerViewSet.as_view({'post': 'agent_heartbeat'}),
        name='server-agent-heartbeat',
    ),
    path('deployments/remote-trigger/', RemoteTriggerView.as_view(), name='deployment-remote-trigger'),
    # Docker Registry token auth
    path('registry/auth/', registry_token, name='registry-token'),
    # Device trust — hardware fingerprint-based device enrollment
    path('devices/register/', register_device, name='device-register'),
    path('devices/', list_devices, name='device-list'),
    path('devices/<int:device_id>/revoke/', revoke_device, name='device-revoke'),
    # Recovery phrase — 12-word BIP39 last-resort account recovery
    path('auth/recovery/generate/', recovery_phrase_generate, name='recovery-generate'),
    path('auth/recovery/verify/', recovery_phrase_verify, name='recovery-verify'),
]
