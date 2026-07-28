import uuid

from django.conf import settings
from django.db import models


class NotificationPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    event_type = models.CharField(choices=[  # type: ignore[var-annotated]
        ('deploy_success', 'Deploy Success'),
        ('deploy_failed', 'Deploy Failed'),
        ('health_alert', 'Health Alert'),
        ('billing_due', 'Billing Due'),
        ('ssl_expiring', 'SSL Expiring'),
        ('backup_completed', 'Backup Completed'),
        ('replication_lag', 'Replication Lag'),
        ('replication_node_down', 'Replication Node Down'),
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
        indexes = [
            models.Index(fields=["user", "read", "-created_at"], name="notif_user_read_created_idx"),
        ]
