"""
Tunnel models for SMSLY development tunnels.

Manages tunnel lifecycle when developers use `npx @smsly/tunnel` to
expose local servers. Tunnels register via the API, and the dashboard
displays active connections with request inspection.
"""
import uuid
from django.db import models
from django.conf import settings


class Tunnel(models.Model):
    """An active development tunnel exposing a local port."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tunnels')
    subdomain = models.CharField(max_length=63, unique=True)
    public_url = models.URLField()
    local_port = models.IntegerField()
    is_active = models.BooleanField(default=True)
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
