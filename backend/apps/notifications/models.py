import uuid

from django.conf import settings
from django.db import models

from apps.deployments.models import Service  # type: ignore[attr-defined]


class NotificationPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    event_type = models.CharField(choices=[  # type: ignore[var-annotated]
        ('deploy_success', 'Deploy Success'),
        ('deploy_failed', 'Deploy Failed'),
        ('health_alert', 'Health Alert'),
        ('billing_due', 'Billing Due'),
        ('ssl_expiring', 'SSL Expiring'),
        ('backup_completed', 'Backup Completed'),
    ], max_length=50)
    channels = models.JSONField(default=list)  # type: ignore[var-annotated] # ['email', 'webhook', 'in_app']

    class Meta:
        unique_together = ('user', 'event_type')

class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    title = models.CharField(max_length=200)  # type: ignore[var-annotated]
    message = models.TextField()  # type: ignore[var-annotated]
    event_type = models.CharField(max_length=50)  # type: ignore[var-annotated]
    read = models.BooleanField(default=False)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ['-created_at']

class ResourceAlert(models.Model):
    """Tracks resource usage alerts for services."""
    class Severity(models.TextChoices):
        INFO = 'INFO', 'Info'
        WARNING = 'WARNING', 'Warning'
        CRITICAL = 'CRITICAL', 'Critical'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service, on_delete=models.CASCADE, related_name='resource_alerts')
    severity = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=Severity.choices, default=Severity.WARNING)
    metric = models.CharField(max_length=50)  # type: ignore[var-annotated]  # 'cpu', 'memory', 'disk'
    threshold = models.FloatField()  # type: ignore[var-annotated]  # percentage
    current_value = models.FloatField()  # type: ignore[var-annotated]
    message = models.TextField()  # type: ignore[var-annotated]
    acknowledged = models.BooleanField(default=False)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
