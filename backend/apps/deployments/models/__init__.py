"""
Deployments models hub.

This module acts as the central entry point for all models in the deployments app.
Models are split into several files to manage complexity, and are unified here
to ensure Django recognizes them for migrations and administrative purposes.
"""

# pylint: disable=unused-import, wrong-import-position

# 1. Base / Core models (Must be first to avoid circularity in sub-models)
# 2. Sub-models (Imported after core models)
from .addons import Addon, Backup  # noqa: F401
from apps.core.models.audit import AuditLog, WebhookDelivery  # noqa: F401
from apps.cloud.models.backup import (  # noqa: F401
    BackupEncryptionKey,
    BackupSchedule,
    ServerBackup,
    ServiceBackup,
    ServiceSnapshot,
    SnapshotSchedule,
)
from .bundles import Bundle, BundleBackup, BundleComponent  # noqa: F401
from apps.cloud.models.cloud_storage import CloudStorageDestination  # noqa: F401
from .core import (  # noqa: F401
    ComplianceProfile,
    Deployment,
    EnvironmentVariable,
    ManagedServer,
    PlatformConfig,
    Project,
    Region,
    Service,
    TrustedDevice,
)
from .cron import CronJob  # noqa: F401
from .network_scope import ScopedNetwork  # noqa: F401
from apps.organizations.models.project import ProjectMember  # noqa: F401
from .database_replica import DatabaseReplica  # noqa: F401
from .ecosystem import EcosystemPlan  # noqa: F401
from apps.cloud.models.github_app import GitHubAppInstallation  # noqa: F401
from .election import (  # noqa: F401
    ClusterState,
    ElectionVote,
    HeartbeatLog,
)
from .mesh import MeshNetwork, WireGuardPeer  # noqa: F401
from apps.autoscaler.models.metrics import ServiceMetric  # noqa: F401
from .registry import RegistryCredential  # noqa: F401
from .registry_scope import ScopedRegistry  # noqa: F401
from apps.autoscaler.models.replica import ServiceReplica  # noqa: F401
from .safedeploy import (  # noqa: F401
    DatabaseClone,
    DeploymentApproval,
    DeploymentArtifact,
    HealthCheckResult,
    MigrationValidation,
    PreviewEnvironment,
)
from .storage import Volume  # noqa: F401
from .templates import Template  # noqa: F401
from .traffic import ServiceTrafficLog  # noqa: F401
from .transfer import ServerTransfer  # noqa: F401
from .tunnels import ReservedSubdomain, Tunnel, TunnelRequest  # noqa: F401
from .updates import PlatformUpdate  # noqa: F401
from apps.core.models.api_token import APIToken  # noqa: F401

# pylint: enable=unused-import, wrong-import-position
