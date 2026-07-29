"""Per-service IP traffic log model."""
import uuid

from django.db import models


class ServiceTrafficLog(models.Model):
    """Per-service IP traffic log. Rows are upserted by the log collector
    Celery task and geolocated asynchronously by the geo resolver task."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(
        'deployments.Service',
        on_delete=models.CASCADE,
        related_name='traffic_logs',
    )
    ip_address = models.GenericIPAddressField()
    domain = models.CharField(max_length=255, blank=True, default='')
    country_code = models.CharField(max_length=2, blank=True, default='')
    country_name = models.CharField(max_length=100, blank=True, default='')
    city = models.CharField(max_length=200, blank=True, default='')
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    request_count = models.PositiveIntegerField(default=1)
    last_seen = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    geo_resolved = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['service', 'country_code']),
            models.Index(fields=['ip_address']),
            models.Index(fields=['geo_resolved']),
            models.Index(fields=['-last_seen']),
        ]
        unique_together = [('service', 'ip_address', 'domain')]

    def __str__(self):
        return f"{self.service_id} {self.ip_address} -> {self.country_code}"
