import uuid

from django.conf import settings
from django.db import models


class EcosystemPlan(models.Model):
    """Persisted ecosystem scan/deploy plan for resume capability."""

    class Status(models.TextChoices):
        SCANNING = 'scanning', 'Scanning'
        REVIEW = 'review', 'Ready for Review'
        DEPLOYING = 'deploying', 'Deploying'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ecosystem_plans')

    # Task IDs for resume
    scan_task_id = models.CharField(max_length=255, blank=True, null=True)
    deploy_task_id = models.CharField(max_length=255, blank=True, null=True)

    # Plan data
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCANNING)
    selected_repos = models.JSONField(default=list, blank=True)
    ai_provider = models.CharField(max_length=50, blank=True, null=True)
    plan = models.JSONField(null=True, blank=True)

    # Results
    services_created = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True, null=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['scan_task_id']),
            models.Index(fields=['deploy_task_id']),
        ]

    def __str__(self):
        return f"EcosystemPlan {self.id} - {self.user.username} - {self.status}"
