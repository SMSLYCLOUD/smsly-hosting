"""Models Cron module."""
import uuid

from django.db import models


class CronJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    # Use string reference to avoid circular import
    service = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.Service',
        on_delete=models.CASCADE,
        related_name='cron_jobs')

    cloud_destination = models.ForeignKey(  # type: ignore[var-annotated]
        'cloud.CloudStorageDestination',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cron_jobs',
        help_text="Optional cloud storage destination to upload cron execution logs"
    )

    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    schedule = models.CharField(max_length=100,  # type: ignore[var-annotated]
                                help_text="Cron schedule e.g. '*/5 * * * *'")
    command = models.CharField(  # type: ignore[var-annotated]
        max_length=500,
        help_text="Command to execute inside the container")

    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]
    last_run_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    next_run_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.name} ({self.schedule})"
