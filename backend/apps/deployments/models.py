"""
Deployments models hub.

This module acts as the central entry point for all models in the deployments app.
Models are split into several files to manage complexity, and are unified here
to ensure Django recognizes them for migrations and administrative purposes.
"""

# pylint: disable=unused-import, wrong-import-position

# 1. Base / Core models (Must be first to avoid circularity in sub-models)
from .models_core import (  # noqa: F401
    ManagedServer,
    Project,
    Region,
    Service,
    Deployment,
    EnvironmentVariable,
    PlatformConfig,
    TrustedDevice,
)

# 2. Sub-models (Imported after core models)
from .models_addons import Addon, Backup  # noqa: F401
from .models_audit import AuditLog, WebhookDelivery  # noqa: F401
from .models_backup import (  # noqa: F401
    BackupSchedule,
    ServiceBackup,
    ServerBackup,
    BackupEncryptionKey,
)
from .models_cloud_storage import CloudStorageDestination  # noqa: F401
from .models_cron import CronJob  # noqa: F401
from .models_database_replica import DatabaseReplica  # noqa: F401
from .models_ecosystem import EcosystemPlan  # noqa: F401
from .models_election import (  # noqa: F401
    ClusterState,
    HeartbeatLog,
    ElectionVote,
)
from .models_mesh import MeshNetwork, WireGuardPeer  # noqa: F401
from .models_metrics import ServiceMetric  # noqa: F401
from .models_replica import ServiceReplica  # noqa: F401
from .models_safedeploy import (  # noqa: F401
    PreviewEnvironment,
    DatabaseClone,
    MigrationValidation,
    DeploymentApproval,
    DeploymentArtifact,
    HealthCheckResult,
)
from .models_storage import Volume  # noqa: F401
from .models_templates import Template  # noqa: F401
from .models_transfer import ServerTransfer  # noqa: F401
from .models_tunnels import Tunnel, TunnelRequest, ReservedSubdomain  # noqa: F401
from .models_updates import PlatformUpdate  # noqa: F401
from .models_registry import RegistryCredential  # noqa: F401
from .models_registry_scope import ScopedRegistry  # noqa: F401
from .models_traffic import ServiceTrafficLog  # noqa: F401
from .models_bundles import Bundle, BundleComponent, BundleBackup  # noqa: F401

# pylint: enable=unused-import, wrong-import-position