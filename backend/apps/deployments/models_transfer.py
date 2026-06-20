import logging
import uuid

from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField

from .models_backup import ServerBackup, ServiceBackup
from .models_core import Service

logger = logging.getLogger(__name__)


class ServerTransfer(models.Model):
    """Tracks migration of services from source to target server."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='server_transfers',
    )
    status = models.CharField(choices=[
        ('PREPARING', 'Preparing'),        # creating backup on source
        ('UPLOADING', 'Uploading'),         # transferring to target
        ('RESTORING', 'Restoring'),         # restoring on target
        ('DNS_CUTOVER', 'DNS Cutover'),     # waiting for DNS propagation
        ('VERIFYING', 'Verifying'),         # health checks on target
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('ROLLED_BACK', 'Rolled Back'),
        ('CANCELLED', 'Cancelled'),
    ], default='PREPARING', max_length=20)

    # Source
    source_server_ip = models.GenericIPAddressField()
    source_server_id = models.CharField(max_length=255, blank=True, default='', help_text='ManagedServer UUID when source is a known node')
    # Encrypted at rest via EncryptedTextField; cleared on COMPLETED/FAILED.
    source_ssh_key = EncryptedTextField(blank=True, default='')
    source_ssh_password = EncryptedCharField(max_length=255, blank=True, default='')
    source_backup = models.ForeignKey(ServiceBackup, on_delete=models.SET_NULL, null=True, blank=True)
    source_server_backup = models.ForeignKey(ServerBackup, on_delete=models.SET_NULL, null=True, blank=True)

    # Target
    target_server_ip = models.GenericIPAddressField()
    # Encrypted at rest via EncryptedTextField; cleared on COMPLETED/FAILED.
    target_ssh_key = EncryptedTextField(blank=True, default='')
    target_ssh_password = EncryptedCharField(max_length=255, blank=True, default='')

    # Free-form JSON bag used to snapshot pre-transfer state (e.g. original
    # service env vars) so rollback can restore it.  Kept on the row so it
    # survives process restarts and is read in the same atomic step that
    # flips the transfer to ROLLED_BACK.
    metadata = models.JSONField(default=dict, blank=True)

    # Scope
    transfer_type = models.CharField(choices=[
        ('SERVICE', 'Single Service'), ('FULL', 'Full Server'),
    ], max_length=20)
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)

    # Progress
    progress_percent = models.IntegerField(default=0)
    current_step = models.CharField(max_length=200, blank=True)
    logs = models.TextField(blank=True)
    error_message = models.TextField(blank=True)

    # Timing
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    estimated_downtime_seconds = models.IntegerField(default=0)

    # Federated Dashboard Sync
    is_incoming = models.BooleanField(default=False, help_text="True if this node is the target")
    source_node_id = models.CharField(max_length=255, blank=True, null=True, help_text="ID/IP of the initiating node")

    # Rollback
    can_rollback = models.BooleanField(default=True)
    rollback_deadline = models.DateTimeField(null=True)  # after this, source cleaned up

    # Cross-platform migration target domain
    target_public_domain = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Target platform domain for cross-platform migration (e.g., app.interserver.com)',
    )

    def save(self, *args, **kwargs):
        from apps.deployments.services.transfer_service import _redact_transfer_text
        for field in ('logs', 'error_message'):
            value = getattr(self, field, None)
            if not value:
                continue
            try:
                redacted = _redact_transfer_text(str(value))
            except Exception as exc:
                logger.warning(
                    "ServerTransfer.save: redaction failed for %s on %s: %s",
                    field, getattr(self, 'id', None), exc,
                )
                continue
            setattr(self, field, redacted)
        super().save(*args, **kwargs)
