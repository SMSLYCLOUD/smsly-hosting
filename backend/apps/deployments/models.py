"""
Deployments models hub.

This module acts as the central entry point for all models in the deployments app.
Models are split into several files to manage complexity, and are unified here
to ensure Django recognizes them for migrations and administrative purposes.
"""

# pylint: disable=unused-import, wrong-import-position

# 1. Base / Core models (Must be first to avoid circularity in sub-models)
# 2. Sub-models (Imported after core models)
from .models_addons import Addon, Backup  # noqa: F401
from .models_audit import AuditLog, WebhookDelivery  # noqa: F401
from .models_backup import (  # noqa: F401
    BackupEncryptionKey,
    BackupSchedule,
    ServerBackup,
    ServiceBackup,
)
from .models_bundles import Bundle, BundleBackup, BundleComponent  # noqa: F401
from .models_cloud_storage import CloudStorageDestination  # noqa: F401
from .models_core import (  # noqa: F401
    Deployment,
    EnvironmentVariable,
    ManagedServer,
    PlatformConfig,
    Project,
    Region,
    Service,
    TrustedDevice,
)
from .models_cron import CronJob  # noqa: F401
from .models_database_replica import DatabaseReplica  # noqa: F401
from .models_ecosystem import EcosystemPlan  # noqa: F401
from .models_github_app import GitHubAppInstallation  # noqa: F401
from .models_election import (  # noqa: F401
    ClusterState,
    ElectionVote,
    HeartbeatLog,
)
from .models_mesh import MeshNetwork, WireGuardPeer  # noqa: F401
from .models_metrics import ServiceMetric  # noqa: F401
from .models_registry import RegistryCredential  # noqa: F401
from .models_registry_scope import ScopedRegistry  # noqa: F401
from .models_replica import ServiceReplica  # noqa: F401
from .models_safedeploy import (  # noqa: F401
    DatabaseClone,
    DeploymentApproval,
    DeploymentArtifact,
    HealthCheckResult,
    MigrationValidation,
    PreviewEnvironment,
)
from .models_storage import Volume  # noqa: F401
from .models_templates import Template  # noqa: F401
from .models_traffic import ServiceTrafficLog  # noqa: F401
from .models_transfer import ServerTransfer  # noqa: F401
from .models_tunnels import ReservedSubdomain, Tunnel, TunnelRequest  # noqa: F401
from .models_updates import PlatformUpdate  # noqa: F401

# pylint: enable=unused-import, wrong-import-position
