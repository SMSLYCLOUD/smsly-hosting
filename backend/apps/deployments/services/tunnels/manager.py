"""
# pylint: disable=missing-class-docstring
Tunnel Manager - Django Integration

Provides Django models and views for tunnel management via the dashboard.
"""

from apps.deployments.models import Service
from django.conf import settings
from django.db import models


class TunnelSession(models.Model):
    """
    Tracks active and historical tunnel sessions.
    """
    class Meta:  # pylint: disable=too-few-public-methods,missing-class-docstring
        ordering = ['-created_at']

    TIER_CHOICES = [
        ('free', 'Free'),
        ('pro', 'Pro'),
        ('team', 'Team'),
    ]

    tunnel_id = models.CharField(max_length=36, unique=True)
    subdomain = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tunnel_sessions',
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optional: link to a service for context",
    )

    # Configuration
    local_port = models.IntegerField()
    tier = models.CharField(
        max_length=10,
        choices=TIER_CHOICES,
        default='free')
    is_custom_subdomain = models.BooleanField(default=False)

    # Status
    is_active = models.BooleanField(default=True)
    request_count = models.IntegerField(default=0)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def public_url(self) -> str:
        """Get the full public URL."""
        base_domain = getattr(settings, 'TUNNEL_BASE_DOMAIN', 'tunnel.localhost')
        return f"https://{self.subdomain}.{base_domain}"

    def __str__(self):
        return f"{self.subdomain} -> localhost:{self.local_port}"


class TunnelRequestLog(models.Model):
    """
    Logged HTTP request through a tunnel (for inspector).
    """
    class Meta:  # pylint: disable=too-few-public-methods,missing-class-docstring
        ordering = ['-timestamp']

    session = models.ForeignKey(
        TunnelSession,
        on_delete=models.CASCADE,
        related_name='requests',
    )
    request_id = models.CharField(max_length=36)

    # Request data
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=2048)
    headers = models.JSONField(default=dict)
    body = models.BinaryField(null=True, blank=True)

    # Response data
    response_status = models.IntegerField(null=True)
    response_time_ms = models.IntegerField(null=True)

    # Metadata
    timestamp = models.DateTimeField(auto_now_add=True)
    is_replay = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.method} {self.path} -> {self.response_status}"


class ReservedSubdomain(models.Model):
    """
    Reserved custom subdomains for Pro/Team users.
    """
    subdomain = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reserved_subdomains',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.subdomain)
