"""Platform self-update tracking model."""
import uuid
from django.db import models


class PlatformUpdate(models.Model):
    """Tracks platform self-updates with rollback capability."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PULLING = 'PULLING', 'Pulling Images'
        BACKING_UP = 'BACKING_UP', 'Backing Up'
        MIGRATING = 'MIGRATING', 'Running Migrations'
        RESTARTING = 'RESTARTING', 'Restarting Services'
        HEALTH_CHECK = 'HEALTH_CHECK', 'Health Check'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'
        ROLLED_BACK = 'ROLLED_BACK', 'Rolled Back'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING)

    # Version tracking
    from_version = models.CharField(max_length=50, blank=True)
    to_version = models.CharField(max_length=50, blank=True)
    from_commit = models.CharField(max_length=40, blank=True)
    to_commit = models.CharField(max_length=40, blank=True)

    # Progress
    progress_percent = models.IntegerField(default=0)
    current_step = models.CharField(max_length=200, blank=True)
    logs = models.TextField(blank=True)

    # Rollback data
    snapshot_data = models.JSONField(
        default=dict, blank=True,
        help_text="Snapshot of container image tags before update")
    can_rollback = models.BooleanField(default=True)
    rollback_deadline = models.DateTimeField(null=True, blank=True)

    # Error
    error_message = models.TextField(blank=True)

    # Federated Deployment Tracking (Elite Feature)
    node_statuses = models.JSONField(
        default=dict, blank=True,
        help_text="Tracks update status per Lite Agent node {node_id: status}")
    
    addon_compatibility_results = models.JSONField(
        default=dict, blank=True,
        help_text="Results of pre-update addon compatibility checks")
    
    fleet_progress = models.JSONField(
        default=dict, blank=True,
        help_text="Step-by-step progress per node")

    # Timing
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    initiated_by = models.CharField(
        max_length=50, default='manual',
        help_text="'manual', 'auto', or 'api'")

    class Meta:
        ordering = ['-created_at']

    def append_log(self, message: str):
        """Thread-safe log append."""
        import datetime
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self.logs += f"[{ts}] {message}\n"
        self.save(update_fields=['logs'])

    def __str__(self):
        return f"Update {self.id} ({self.status})"
