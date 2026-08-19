import contextlib
import logging
import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from .core import TimeStampedModel
from .service import Service

logger = logging.getLogger(__name__)


class Deployment(TimeStampedModel):
    """
    Represents a single deployment of a service.
    """
    # pylint: disable=too-many-ancestors
    class Status(models.TextChoices):
        """Deployment statuses."""
        QUEUED = 'QUEUED', _('Queued')
        REVIEW = 'REVIEW', _('Review')
        BUILDING = 'BUILDING', _('Building')
        BUILD_FAILED = 'BUILD_FAILED', _('Build Failed')
        AWAITING_APPROVAL = 'AWAITING_APPROVAL', _('Awaiting Approval')
        BACKUP_RUNNING = 'BACKUP_RUNNING', _('Backup Running')
        BACKUP_FAILED = 'BACKUP_FAILED', _('Backup Failed')
        MIGRATION_PLANNING = 'MIGRATION_PLANNING', _('Migration Planning')
        MIGRATION_RUNNING = 'MIGRATION_RUNNING', _('Migration Running')
        MIGRATION_FAILED = 'MIGRATION_FAILED', _('Migration Failed')
        DEPLOYING = 'DEPLOYING', _('Deploying')
        HEALTH_CHECK = 'HEALTH_CHECK', _('Health Check')
        HEALTH_CHECK_FAILED = 'HEALTH_CHECK_FAILED', _('Health Check Failed')
        STAGED = 'STAGED', _('Staged')
        ACTIVE = 'ACTIVE', _('Active')
        FAILED = 'FAILED', _('Failed')
        CANCELLED = 'CANCELLED', _('Cancelled')
        INACTIVE = 'INACTIVE', _('Inactive')
        ROLLING_BACK = 'ROLLING_BACK', _('Rolling Back')
        ROLLED_BACK = 'ROLLED_BACK', _('Rolled Back')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='deployments')
    commit_hash = models.CharField(max_length=40)  # type: ignore[var-annotated]
    commit_message = models.TextField(blank=True)  # type: ignore[var-annotated]
    branch = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, default='',
        help_text="Branch name this deployment deploys (overrides service default)")

    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )

    build_logs = models.TextField(blank=True)  # type: ignore[var-annotated]
    runtime_logs_url = models.URLField(blank=True, null=True)  # type: ignore[var-annotated]

    pipeline_stages = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="List of pipeline stages (name, status, duration)")

    ai_diagnosis = models.TextField(  # type: ignore[var-annotated]
        blank=True, help_text="AI suggested fix for failure")

    # Pre-deploy review: stores AI recommendations for user approval
    review_summary = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="AI-recommended resources, env vars, and issues for review")

    # Security
    vulnerability_report = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True, help_text="Trivy scan results")

    container_id = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    remote_deployment_id = models.CharField(  # type: ignore[var-annotated]
        max_length=64,
        blank=True,
        null=True,
        help_text="Deployment ID on a delegated remote server.",
    )

    # Blue-green: stores the new (green) container ID during a deployment
    green_container_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True,
        help_text="Temp container ID for the new version during blue-green swap")

    started_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    finished_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    # Rollback tracking
    is_rollback = models.BooleanField(  # type: ignore[var-annotated]
        default=False, help_text="Whether this deployment is a rollback")
    source_node = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, null=True,
        help_text="Node that triggered this deployment (for multi-deploy)")
    rollback_from = models.ForeignKey(  # type: ignore[var-annotated]
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='rollback_deployments',
        help_text="The deployment this was rolled back from")
    target_server = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.ManagedServer',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='target_deployments',
        help_text="Server this deployment was explicitly routed to")
    target_is_local = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="True when this deployment was explicitly routed to the local controller")

    ecosystem_retry_count = models.IntegerField(  # type: ignore[var-annotated]
        default=0, db_default=0,
        help_text="Number of times ecosystem deploy has retried this deployment")

    queued_min_replicas = models.IntegerField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Snapshot of service.min_replicas captured at queue time so the deploy "
                  "executor uses the original replica count even if the autoscaler mutates "
                  "it during the build.")

    metadata = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Scratch state for in-flight pipeline phases (e.g. pre-migration state for rollback).")

    scan_depth = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=[('shallow', 'Shallow'), ('standard', 'Standard'), ('deep', 'Deep')],
        default='standard',
        blank=True,
        help_text="Scan depth override for this deployment (empty = use service default)",
    )

    ai_resources = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="AI resource recommendations {cpu_cores, memory_mb} from analysis phase",
    )

    # Per-deployment registry override: if set, the builder uses this
    # instead of the scoped chain (ScopedRegistry → PlatformConfig).
    registry_override = models.JSONField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="Per-deployment registry override {url, username, password} "
                  "— builder uses this instead of the scoped chain"
    )

    # Staging / blue-green promote flow
    staging_url = models.URLField(  # type: ignore[var-annotated]
        blank=True, null=True,
        help_text="Temporary staging URL where the green container can be previewed before promote")
    staged_at = models.DateTimeField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="When the deployment entered STAGED status (auto-promote timer starts)")

    # GitHub Deployments API
    github_deployment_id = models.BigIntegerField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="GitHub Deployment ID for status updates via the Deployments API",
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=["service", "status"], name="dep_service_status_idx"),
            models.Index(fields=["service", "-created_at"], name="dep_service_created_idx"),
            models.Index(fields=["status"], name="dep_status_idx"),
        ]

    @property
    def duration_seconds(self) -> float | None:
        """Compute deployment duration in seconds."""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


    # Post-deployment verification metadata
    verified_target_type = models.CharField(  # type: ignore[var-annotated]
        max_length=50,
        blank=True,
        null=True,
        help_text="The verified runtime environment where this deployment actually executed."
    )
    verified_host_ip = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified IP address or hostname where the deployment executed."
    )
    verified_runtime_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="The verified container ID or process ID of the deployment."
    )
    verified_at = models.DateTimeField(  # type: ignore[var-annotated]
        blank=True,
        null=True,
        help_text="When the execution location was verified."
    )
    def __str__(self):
        label = f"{self.service.name} - {self.commit_hash[:7]} ({self.status})"
        if self.is_rollback:
            label = f"[ROLLBACK] {label}"
        return label

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.status == self.Status.ACTIVE and self.service_id:
            from django.db import transaction
            with transaction.atomic():
                locked = Deployment.objects.select_for_update().filter(
                    service_id=self.service_id,
                    status=self.Status.ACTIVE,
                )
                if self.pk:
                    locked = locked.exclude(pk=self.pk)
                if self.remote_deployment_id:
                    with contextlib.suppress(ValueError, TypeError):
                        locked = locked.exclude(pk=uuid.UUID(self.remote_deployment_id))
                locked.update(status=self.Status.INACTIVE)
        super().save(*args, **kwargs)
