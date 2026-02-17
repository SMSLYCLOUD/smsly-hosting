"""Models Audit module."""
import hashlib
import json
from django.db import models
from django.utils import timezone


class AuditLog(models.Model):
    """
    Immutable, hash-linked audit log.
    Ensures that deployment history cannot be tampered with.
    Independent implementation (no external blockchain dependency).
    """
    id = models.BigAutoField(primary_key=True)
    timestamp = models.DateTimeField(default=timezone.now, editable=False)

    actor = models.CharField(
        max_length=255,
        default='system')  # User or System
    # e.g. "DEPLOY_TRIGGER", "SCALE_UP"
    action = models.CharField(max_length=255, default='unknown')
    target = models.CharField(max_length=255,
                              default='none')  # e.g. "Service: my-app"
    metadata = models.JSONField(default=dict)

    # Cryptographic Links
    previous_hash = models.CharField(
        max_length=64, editable=False, default='0' * 64)
    hash = models.CharField(
        max_length=64,
        editable=False,
        unique=True,
        default='0' * 64)

    class Meta:
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
            "action": self.action,
            "target": self.target,
            "meta": self.metadata
        }
        # Sort keys for consistent hashing
        payload_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(payload_str.encode('utf-8')).hexdigest()

    def save(self, *args, **kwargs):
        if not self.pk:  # Only on creation
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
                return

        super().save(*args, **kwargs)

    def __str__(self):
        return f"[{self.hash[:8]}] {self.action} by {self.actor}"
