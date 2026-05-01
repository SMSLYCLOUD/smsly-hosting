import uuid
from django.db import models
from django.utils import timezone
from .models_core import Service
from .models_backup import ServiceBackup, ServerBackup

class ServerTransfer(models.Model):
    """Tracks migration of services from source to target server."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    status = models.CharField(choices=[
        ('PREPARING', 'Preparing'),        # creating backup on source
        ('UPLOADING', 'Uploading'),         # transferring to target
        ('RESTORING', 'Restoring'),         # restoring on target
        ('DNS_CUTOVER', 'DNS Cutover'),     # waiting for DNS propagation
        ('VERIFYING', 'Verifying'),         # health checks on target
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('ROLLED_BACK', 'Rolled Back'),
    ], default='PREPARING', max_length=20)

    # Source
    source_server_ip = models.GenericIPAddressField()
    source_backup = models.ForeignKey(ServiceBackup, on_delete=models.SET_NULL, null=True, blank=True)
    source_server_backup = models.ForeignKey(ServerBackup, on_delete=models.SET_NULL, null=True, blank=True)

    # Target
    target_server_ip = models.GenericIPAddressField()
    target_ssh_key = models.TextField(blank=True)  # encrypted SSH key for target
    target_ssh_password = models.CharField(max_length=255, blank=True, default='')  # SSH password for target

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
