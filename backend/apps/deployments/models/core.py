"""Core models for Deployments app."""
import logging

from django.db import models

logger = logging.getLogger(__name__)


class TimeStampedModel(models.Model):
    """Abstract base class with created_at and updated_at fields."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


from .platform import ManagedServer, PlatformConfig, TrustedDevice  # noqa: E402
from .service import ComplianceProfile, Project, Region, Service  # noqa: E402
from .deployment import Deployment  # noqa: E402
from .environment import EnvironmentVariable  # noqa: E402
