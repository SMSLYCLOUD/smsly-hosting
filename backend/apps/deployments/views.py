"""
Views re-export shim.
This file has been refactored into domain-specific modules.
These imports are kept for backwards compatibility with `urls.py` and tests.
"""

from .views_service import ServiceViewSet
from .views_deployment import DeploymentViewSet, RemoteTriggerView
from .views_backup import ServiceBackupViewSet, ServerBackupViewSet, BackupScheduleViewSet
from .views_system import SystemConfigView, PlatformResourcesView, RouteRecheckView
from .views_domains import DomainConfigView
from .views_audit import AuditLogViewSet
from .views_auth import SessionTokenView, ZeroTrustHMACAuthentication, CaddySecretOrAdminPermission
from .views_route_status import RouteStatusView

# some tests might still import `_redact_caddyfile_preview` from views
from .views_system import _redact_caddyfile_preview
