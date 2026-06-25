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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ecosystem_plans')  # type: ignore[var-annotated]

    # Task IDs for resume
    scan_task_id = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]
    deploy_task_id = models.CharField(max_length=255, blank=True, null=True)  # type: ignore[var-annotated]

    # Plan data
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SCANNING)  # type: ignore[var-annotated]
    selected_repos = models.JSONField(default=list, blank=True)  # type: ignore[var-annotated]
    ai_provider = models.CharField(max_length=50, blank=True, null=True)  # type: ignore[var-annotated]
    plan = models.JSONField(null=True, blank=True)  # type: ignore[var-annotated]

    # Scan progress (persistent so resume shows where we left off)
    scan_progress = models.TextField(blank=True, null=True)  # type: ignore[var-annotated]

    # Results
    services_created = models.JSONField(default=list, blank=True)  # type: ignore[var-annotated]
    error_message = models.TextField(blank=True, null=True)  # type: ignore[var-annotated]

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]
    completed_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['scan_task_id']),
            models.Index(fields=['deploy_task_id']),
        ]

    def __str__(self):
        return f"EcosystemPlan {self.id} - {self.user.username} - {self.status}"
