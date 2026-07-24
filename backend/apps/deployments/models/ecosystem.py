import uuid

from django.conf import settings
from django.db import models


class EcosystemPlan(models.Model):
    """Persisted ecosystem scan/deploy plan for resume capability.

    Project-scoped — all services created by the deploy task are
    assigned to the plan's project for isolation and permission
    management.
    """

    class Status(models.TextChoices):
        SCANNING = 'scanning', 'Scanning'
        REVIEW = 'review', 'Ready for Review'
        DEPLOYING = 'deploying', 'Deploying'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ecosystem_plans')  # type: ignore[var-annotated]
    project = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='ecosystem_plans',
        help_text='Project this ecosystem plan belongs to. All created services are scoped to this project.',
    )
    use_shared_addons = models.BooleanField(
        default=True,
        help_text='When True, addons (Postgres, Redis, etc.) are provisioned once and shared across all services. When False, each service provisions its own addons independently.',
    )
    cancel_others_on_failure = models.BooleanField(
        default=False,
        help_text='When True, if any service deployment fails, all remaining queued deployments in the ecosystem are cancelled.',
    )
    shared_addon_config = models.JSONField(
        default=dict,
        blank=True,
        help_text='Per-addon sharing configuration. Keys are addon types (e.g. "POSTGRES", "REDIS"), values are objects with "shared" (bool) and optionally "shared_by" (list of service names). When an addon is not listed, the use_shared_addons default applies.',
    )

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
            models.Index(fields=['project', 'status']),
            models.Index(fields=['scan_task_id']),
            models.Index(fields=['deploy_task_id']),
        ]

    def __str__(self):
        proj = f" project={self.project_id}" if self.project_id else ""
        return f"EcosystemPlan {self.id} - {self.user.username}{proj} - {self.status}"
