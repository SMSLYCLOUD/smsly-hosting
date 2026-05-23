"""
Deployments models hub.

This module acts as the central entry point for all models in the deployments app.
Models are split into several files to manage complexity, and are unified here
to ensure Django recognizes them for migrations and administrative purposes.
"""

# pylint: disable=unused-import, wrong-import-position

# 1. Base / Core models (Must be first to avoid circularity in sub-models)
from .models_core import (
    TimeStampedModel,
    Region,
    Service,
    ComplianceProfile,
    EnvironmentVariable,
    Deployment,
    PlatformConfig,
    ManagedServer,
    Project,
)

# 2. Sub-models (Imported after core models)
from .models_audit import AuditLog
from .models_cron import CronJob
from .models_storage import Volume
from .models_updates import PlatformUpdate
from .api_token_auth import APIToken
# Remainder are imported in order
from .models_safedeploy import PreviewEnvironment, DatabaseClone, MigrationValidation, DeploymentApproval, DeploymentArtifact, HealthCheckResult
from .models_addons import Addon, Backup
from .models_backup import ServiceBackup, ServerBackup, BackupSchedule
from .models_election import ClusterState, HeartbeatLog, ElectionVote
from .models_mesh import MeshNetwork, WireGuardPeer
from .models_metrics import ServiceMetric
from .models_templates import Template
from .models_transfer import ServerTransfer
from .models_tunnels import Tunnel, TunnelRequest, ReservedSubdomain
from .models_ecosystem import EcosystemPlan

# pylint: enable=unused-import, wrong-import-position
