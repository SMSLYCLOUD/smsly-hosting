"""Bundle models for grid.addons custom infrastructure bundles.

Tracks the state of custom addon bundles (e.g. Kamailio, FreeSWITCH,
LiveKit) declared in ``grid.addons`` manifest files.  Bundle components
appear in the service's Addons tab alongside standard addon types so
users can manage them uniformly.
"""
import uuid

from django.db import models

from .models_core import Service, TimeStampedModel


class Bundle(TimeStampedModel):
    """A custom infrastructure bundle declared in ``grid.addons``."""

    class Status(models.TextChoices):
        PROVISIONING = 'PROVISIONING', 'Provisioning'
        ACTIVE = 'ACTIVE', 'Active'
        FAILED = 'FAILED', 'Failed'
        DELETED = 'DELETED', 'Deleted'
        DELETION_PENDING = 'DELETION_PENDING', 'Deletion Pending'
        DELETION_FAILED = 'DELETION_FAILED', 'Deletion Failed'

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='bundles',
    )
    name = models.CharField(
        max_length=255,
        help_text="Bundle name from grid.addons (e.g. 'sip-stack')",
    )
    network = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Docker network name for the bundle",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING,
        db_index=True,
    )
    grid_addons_hash = models.CharField(
        max_length=128, blank=True, default='',
        help_text="SHA-256 of the grid.addons file at deploy time. "
                  "Used to detect when a rebuild is needed.",
    )
    deletion_error = models.TextField(blank=True, default='')

    class Meta:
        unique_together = ('service', 'name')
        ordering = ['name']

    def __str__(self):
        return f"Bundle {self.name} for {self.service.name}"


class BundleComponent(TimeStampedModel):
    """A single service within a bundle (e.g. 'kamailio', 'rtpengine')."""

    class Status(models.TextChoices):
        PROVISIONING = 'PROVISIONING', 'Provisioning'
        ACTIVE = 'ACTIVE', 'Active'
        FAILED = 'FAILED', 'Failed'
        STOPPED = 'STOPPED', 'Stopped'

    class SourceType(models.TextChoices):
        IMAGE = 'IMAGE', 'Docker Image'
        REPO = 'REPO', 'Git Repository'

    class HealthStatus(models.TextChoices):
        UNKNOWN = 'unknown', 'Unknown'
        HEALTHY = 'healthy', 'Healthy'
        UNHEALTHY = 'unhealthy', 'Unhealthy'
        STARTING = 'starting', 'Starting'

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
    )
    bundle = models.ForeignKey(
        Bundle,
        on_delete=models.CASCADE,
        related_name='components',
    )
    name = models.CharField(
        max_length=255,
        help_text="Component name from grid.addons (e.g. 'kamailio')",
    )
    source_type = models.CharField(
        max_length=10,
        choices=SourceType.choices,
        default=SourceType.IMAGE,
    )
    image = models.CharField(
        max_length=512, blank=True, default='',
        help_text="Docker image (for IMAGE source type)",
    )
    repo = models.CharField(
        max_length=512, blank=True, default='',
        help_text="Git repo URL (for REPO source type)",
    )
    branch = models.CharField(
        max_length=255, blank=True, default='main',
    )
    build_type = models.CharField(
        max_length=20, blank=True, default='',
        help_text="'dockerfile' or 'nixpacks'",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING,
        db_index=True,
    )
    container_name = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Docker container name",
    )
    container_id = models.CharField(
        max_length=64, blank=True, default='',
        help_text="Short Docker container ID",
    )
    connection_url = models.CharField(
        max_length=512, blank=True, default='',
        help_text="Connection URL for this component",
    )
    ports = models.JSONField(
        default=list, blank=True,
        help_text="Port mappings from grid.addons",
    )
    health_status = models.CharField(
        max_length=20,
        choices=HealthStatus.choices,
        default=HealthStatus.UNKNOWN,
        blank=True,
    )

    class Meta:
        unique_together = ('bundle', 'name')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.bundle.name})"

    def save(self, *args, **kwargs):
        # Auto-sync source_type with image/repo fields
        if self.repo and not self.image:
            self.source_type = self.SourceType.REPO
        elif self.image:
            self.source_type = self.SourceType.IMAGE
        super().save(*args, **kwargs)

    @property
    def parsed_credentials(self) -> dict:
        """Parse connection_url into env-var-style credentials."""
        from urllib.parse import urlparse, unquote
        if not self.connection_url:
            return {}
        parsed = urlparse(self.connection_url)
        slug = self.name.upper().replace('-', '_')
        result = {f'{slug}_URL': self.connection_url}
        if parsed.hostname:
            result[f'{slug}_HOST'] = parsed.hostname
        if parsed.port:
            result[f'{slug}_PORT'] = str(parsed.port)
        if parsed.username:
            result[f'{slug}_USER'] = unquote(parsed.username)
        if parsed.password:
            result[f'{slug}_PASSWORD'] = unquote(parsed.password)
        if parsed.path and parsed.path != '/':
            result[f'{slug}_DATABASE'] = unquote(parsed.path.lstrip('/'))
        return result


class BundleBackup(TimeStampedModel):
    """Backup record for a bundle component."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False,
    )
    component = models.ForeignKey(
        BundleComponent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='backups',
    )
    file_path = models.CharField(max_length=512, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"BundleBackup {self.id} ({self.status})"
