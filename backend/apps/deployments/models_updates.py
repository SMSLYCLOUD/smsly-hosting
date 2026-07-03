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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    status = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=Status.choices, default=Status.PENDING)

    # Version tracking
    from_version = models.CharField(max_length=50, blank=True)  # type: ignore[var-annotated]
    to_version = models.CharField(max_length=50, blank=True)  # type: ignore[var-annotated]
    from_commit = models.CharField(max_length=40, blank=True)  # type: ignore[var-annotated]
    to_commit = models.CharField(max_length=40, blank=True)  # type: ignore[var-annotated]

    # Progress
    progress_percent = models.IntegerField(default=0)  # type: ignore[var-annotated]
    current_step = models.CharField(max_length=200, blank=True)  # type: ignore[var-annotated]
    logs = models.TextField(blank=True)  # type: ignore[var-annotated]

    # Rollback data
    snapshot_data = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Snapshot of container image tags before update")
    can_rollback = models.BooleanField(default=True)  # type: ignore[var-annotated]
    rollback_deadline = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    # Error
    error_message = models.TextField(blank=True)  # type: ignore[var-annotated]

    # Federated Deployment Tracking (Elite Feature)
    node_statuses = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Tracks update status per Lite Agent node {node_id: status}")

    addon_compatibility_results = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Results of pre-update addon compatibility checks")

    fleet_progress = models.JSONField(  # type: ignore[var-annotated]
        default=dict, blank=True,
        help_text="Step-by-step progress per node")

    # Timing
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    completed_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    initiated_by = models.CharField(  # type: ignore[var-annotated]
        max_length=50, default='manual',
        help_text="'manual', 'auto', or 'api'")

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        update_fields = kwargs.get('update_fields')
        if update_fields is None or any(f in update_fields for f in ('status', 'current_step', 'progress_percent', 'error_message')):
            self.broadcast_status()

    def append_log(self, message: str):
        """Thread-safe log append with real-time channel broadcasting."""
        import datetime
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        lines = message.splitlines()
        formatted = "".join(f"[{ts}] {line}\n" for line in lines if line.strip())
        if not formatted:
            return
        self.logs += formatted
        super().save(update_fields=['logs'])
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"platform_update_{self.id}",
                    {
                        "type": "log_message",
                        "log": formatted,
                    }
                )
        except Exception:
            pass

    def broadcast_status(self):
        """Broadcast state updates via channels."""
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            channel_layer = get_channel_layer()
            if channel_layer:
                async_to_sync(channel_layer.group_send)(
                    f"platform_update_{self.id}",
                    {
                        "type": "status_message",
                        "status": self.status,
                        "current_step": self.current_step,
                        "progress_percent": self.progress_percent,
                        "error_message": self.error_message,
                    }
                )
        except Exception:
            pass

    def __str__(self):
        return f"Update {self.id} ({self.status})"
