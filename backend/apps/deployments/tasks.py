# pylint: disable=too-many-lines
"""Tasks module."""
import logging
import re
import shutil
import tempfile
import subprocess
import os
import json
import zipfile
import time
from urllib.parse import unquote, urlparse
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.db.models import Sum
import requests

from apps.cloud.models import CloudProvider
from apps.cloud.services.builder import NixpacksBuilder
from apps.cloud.services.compute import ComputeService
from apps.cloud.services.function_provisioner import FunctionProvisioner
from apps.deployments.services.pipeline import PipelineManager, PipelineError
from apps.deployments.models import Service, Deployment, EnvironmentVariable, PlatformConfig
from apps.deployments.models_addons import Addon, Backup
from apps.deployments.models_storage import Volume
from apps.deployments.utils import (
    append_log,
    broadcast_status,
    update_stage,
)
from apps.billing.services.metering import UsageMeter
from apps.billing.models import UsageRecord, UserSubscription, Invoice, PricingPlan, DailyRevenue, InfrastructureCost
from services.addon_provisioner import addon_provisioner
from .services.backup_service import BackupService
from .services.transfer_service import ServerTransferService
from .models_backup import BackupSchedule, ServiceBackup
from .models_transfer import ServerTransfer

logger = logging.getLogger(__name__)


def _regenerate_caddyfile():
    """Regenerate and apply the Caddyfile with current service domains.

    Called after successful deployments so new services get Caddy site blocks
    (and therefore SSL certificates) without requiring a manual Settings save.
    """
    try:
        config = PlatformConfig.load()
        from services.caddy_manager import generate_caddyfile, apply_caddyfile
        content = generate_caddyfile(config)
        result = apply_caddyfile(content)
        if result.get('ok'):
            logger.info("Caddyfile regenerated after deployment")
        else:
            logger.warning("Caddyfile regeneration failed: %s", result.get('message'))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Could not regenerate Caddyfile: %s", exc)


def _docker_safe_segment(value: str, fallback: str = "app") -> str:
