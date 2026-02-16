"""
Multi-server management model.

Allows controlling multiple SMSLY Hosting instances from a single dashboard.
"""

import uuid

from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField


class ManagedServer(models.Model):
    """
    Represents a remote SMSLY Hosting server that can be controlled
    from this dashboard instance.

    Supports two workflows:
    1. **Connect existing** — user provides api_url + api_token manually.
    2. **Provision new** — user provides SSH credentials; the platform
       SSHes in, runs install.sh, and auto-fills api_url/api_token.
    """

    class Status(models.TextChoices):
        ONLINE = "ONLINE", "Online"
        OFFLINE = "OFFLINE", "Offline"
        UNKNOWN = "UNKNOWN", "Unknown"

    class ProvisionStatus(models.TextChoices):
        NONE = "NONE", "Not provisioned"
        PENDING = "PENDING", "Pending"
        PROVISIONING = "PROVISIONING", "Provisioning"
        DONE = "DONE", "Done"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="managed_servers",
    )
    name = models.CharField(
        max_length=100,
        help_text="Human-readable label, e.g. 'Production VPS' or 'Staging EU'",
    )
    host = models.CharField(
        max_length=255,
        help_text="IP address or domain, e.g. '198.51.100.5' or 'prod.example.com'",
    )

    # ── Connection credentials (filled manually OR by provisioner) ──
    api_url = models.URLField(
        blank=True, default="",
        help_text="Full URL to the SMSLY Hosting API (auto-filled after provisioning)",
    )
    api_token = EncryptedCharField(
        max_length=255, blank=True, default="",
        help_text="Bearer token for the remote API (auto-filled after provisioning)",
    )

    # ── SSH credentials (for provisioning) ──
    ssh_port = models.IntegerField(
        default=22,
        help_text="SSH port for direct server access",
    )
    ssh_user = models.CharField(
        max_length=100, default="root",
        help_text="SSH username (usually root)",
    )
    ssh_password = EncryptedCharField(
        max_length=255, blank=True, default="",
        help_text="SSH password (encrypted at rest)",
    )
    ssh_key = EncryptedTextField(
        blank=True, default="",
        help_text="SSH private key content (encrypted at rest)",
    )

    is_primary = models.BooleanField(
        default=False,
        help_text="Mark one server as the main production server",
    )

    # ── Status ──
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNKNOWN,
    )
    last_health_check = models.DateTimeField(null=True, blank=True)
    server_version = models.CharField(max_length=50, blank=True, default="")
    services_count = models.IntegerField(default=0)

    # ── Provisioning ──
    provision_status = models.CharField(
        max_length=20,
        choices=ProvisionStatus.choices,
        default=ProvisionStatus.NONE,
    )
    provision_logs = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "Managed Server"

    def __str__(self):
        return f"{self.name} ({self.host})"
