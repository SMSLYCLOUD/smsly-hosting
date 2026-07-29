"""Models Audit module."""
import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """
    Immutable, hash-linked audit log.
    Ensures that deployment history cannot be tampered with.
    Independent implementation (no external blockchain dependency).
    """
    id = models.BigAutoField(primary_key=True)  # type: ignore[var-annotated]
    timestamp = models.DateTimeField(default=timezone.now, editable=False)  # type: ignore[var-annotated]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)  # type: ignore[var-annotated]
    project = models.ForeignKey('deployments.Project', on_delete=models.SET_NULL, null=True, blank=True)  # type: ignore[var-annotated]

    actor = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        default='system')  # User or System
    # e.g. "DEPLOY_TRIGGER", "SCALE_UP"
    action = models.CharField(max_length=255, default='unknown')  # type: ignore[var-annotated]
    target = models.CharField(max_length=255,  # type: ignore[var-annotated]
                              default='none')  # e.g. "Service: my-app"
    metadata = models.JSONField(default=dict)  # type: ignore[var-annotated]

    # Cryptographic Links
    previous_hash = models.CharField(  # type: ignore[var-annotated]
        max_length=64, editable=False, default='0' * 64)
    hash = models.CharField(  # type: ignore[var-annotated]
        max_length=64,
        editable=False,
        unique=True,
        default='0' * 64)

    class Meta:
        db_table = 'deployments_auditlog'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['hash']),
            models.Index(fields=['actor']),
        ]

    def calculate_hash(self):
        """
        Computes SHA-256 hash of the record content + previous hash.
        """
        payload = {
            "prev": self.previous_hash,
            "ts": str(self.timestamp),
            "actor": self.actor,
            "user_id": self.user_id,
            "project_id": self.project_id,
            "action": self.action,
            "target": self.target,
            "meta": self.metadata
        }
        # Sort keys for consistent hashing
        payload_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(payload_str.encode('utf-8')).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit logs are immutable and cannot be modified.")

        from django.db import transaction
        with transaction.atomic():
            # H-4 fix: select_for_update on the last log to prevent
            # concurrent writes from reading the same previous_hash
            last_log = (
                AuditLog.objects
                .select_for_update()
                .order_by('-id')
                .first()
            )
            if last_log:
                self.previous_hash = last_log.hash
            else:
                self.previous_hash = "0" * 64  # Genesis block

            # 2. Compute Hash
            self.hash = self.calculate_hash()
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit logs are undeletable.")

    def __str__(self):
        return f"[{self.hash[:8]}] {self.action} by {self.actor}"


class WebhookDelivery(models.Model):
    """
    Idempotency record for incoming webhook deliveries.

    Provider (e.g. GitHub) sends an ``X-GitHub-Delivery`` UUID with every
    webhook. We persist it on first sight so duplicate deliveries of the
    same event do not trigger duplicate builds / deployments.
    """
    STATUS_CHOICES = [
        ('processed', 'Processed'),
        ('failed', 'Failed'),
        ('ignored', 'Ignored'),
    ]

    delivery_id = models.CharField(  # type: ignore[var-annotated]
        max_length=128, primary_key=True,
        help_text="Provider-supplied unique delivery identifier (e.g. X-GitHub-Delivery)."
    )
    provider = models.CharField(  # type: ignore[var-annotated]
        max_length=32, default='github',
        help_text="Webhook provider that produced this delivery."
    )
    event_type = models.CharField(  # type: ignore[var-annotated]
        max_length=64, blank=True, default='',
        help_text="Event type from the provider (push, pull_request, etc.)."
    )
    received_at = models.DateTimeField(default=timezone.now)  # type: ignore[var-annotated]
    status = models.CharField(  # type: ignore[var-annotated]
        max_length=16, choices=STATUS_CHOICES, default='processed')

    class Meta:
        db_table = 'deployments_webhookdelivery'
        indexes = [
            models.Index(fields=['provider', 'received_at']),
        ]

    def __str__(self):
        return f"{self.provider}:{self.delivery_id} ({self.status})"
