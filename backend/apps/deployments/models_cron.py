"""Models Cron module."""
import uuid

from django.db import models


class CronJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Use string reference to avoid circular import
    service = models.ForeignKey(
        'deployments.Service',
        on_delete=models.CASCADE,
        related_name='cron_jobs')

    name = models.CharField(max_length=255)
    schedule = models.CharField(max_length=100,
                                help_text="Cron schedule e.g. '*/5 * * * *'")
    command = models.CharField(
        max_length=500,
        help_text="Command to execute inside the container")

    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.schedule})"
