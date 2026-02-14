"""
Multi-server management model.

Allows controlling multiple SMSLY Hosting instances from a single dashboard.
"""

import uuid

from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class ManagedServer(models.Model):
    """
    Represents a remote SMSLY Hosting server that can be controlled
    from this dashboard instance.
    """

    class Status(models.TextChoices):
        ONLINE = "ONLINE", "Online"
        OFFLINE = "OFFLINE", "Offline"
        UNKNOWN = "UNKNOWN", "Unknown"

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
    api_url = models.URLField(
        help_text="Full URL to the SMSLY Hosting API, e.g. 'https://prod.example.com'",
    )
    api_token = EncryptedCharField(
        max_length=255,
        help_text="Bearer token for authenticating to the remote server's API",
    )
    ssh_port = models.IntegerField(
        default=22,
        help_text="SSH port for direct server access",
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Mark one server as the main production server",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNKNOWN,
    )
    last_health_check = models.DateTimeField(null=True, blank=True)
    server_version = models.CharField(max_length=50, blank=True, default="")
    services_count = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "name"]
        verbose_name = "Managed Server"

    def __str__(self):
        return f"{self.name} ({self.host})"
