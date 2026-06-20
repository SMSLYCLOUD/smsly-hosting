"""
Tunnel models for SMSLY development tunnels.

Manages tunnel lifecycle when developers use `npx @smsly/tunnel` to
expose local servers. Tunnels register via the API, and the dashboard
displays active connections with request inspection.
"""
import uuid

from django.conf import settings
from django.db import models


class Tunnel(models.Model):
    """An active development tunnel exposing a local port."""
    TUNNEL_TYPES = [
        ('http', 'HTTP'),
        ('tcp', 'TCP'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tunnels')
    subdomain = models.CharField(max_length=63, unique=True)
    public_url = models.URLField()
    local_port = models.IntegerField()
    type = models.CharField(max_length=4, choices=TUNNEL_TYPES, default='http')
    is_active = models.BooleanField(default=True)
    shared_with = models.JSONField(default=list, blank=True,
        help_text='List of email addresses this tunnel is shared with')
    bandwidth_bytes = models.BigIntegerField(default=0,
        help_text='Total bandwidth used in bytes')
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subdomain} → :{self.local_port}"

    @property
    def request_count(self):
        return self.requests.count()


class TunnelRequest(models.Model):
    """A captured HTTP request passing through a tunnel."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tunnel = models.ForeignKey(
        Tunnel,
        on_delete=models.CASCADE,
        related_name='requests')
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=2048)
    status = models.IntegerField(null=True, blank=True)
    response_time_ms = models.IntegerField(null=True, blank=True)
    headers = models.JSONField(default=dict, blank=True)
    body_preview = models.TextField(blank=True, default='')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.method} {self.path} → {self.status or 'pending'}"


class ReservedSubdomain(models.Model):
    """A subdomain reserved by a user for persistent tunnel URLs."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reserved_subdomains')
    subdomain = models.CharField(max_length=63, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.subdomain} (reserved by {self.owner})"
