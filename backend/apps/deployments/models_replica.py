import uuid
from django.db import models
from django.conf import settings
from .models_core import Service, ManagedServer


class ServiceReplica(models.Model):
    """Tracks auto-scaled replicas spawned on remote nodes."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='replicas')
    node = models.ForeignKey(
        ManagedServer, on_delete=models.SET_NULL, null=True,
        related_name='hosted_replicas',
        help_text='The remote node where this replica runs',
    )
    container_name = models.CharField(max_length=255)
    container_id = models.CharField(max_length=255, blank=True, default='')

    status = models.CharField(
        max_length=20,
        choices=[
            ('SPAWNING', 'Spawning'),
            ('RUNNING', 'Running'),
            ('DRAINING', 'Draining'),
            ('DESTROYING', 'Destroying'),
            ('DESTROYED', 'Destroyed'),
        ],
        default='SPAWNING',
    )

    metrics_snapshot = models.JSONField(default=dict, help_text='Last known CPU/mem from Prometheus')
    spawn_reason = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    destroyed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['service', 'status']),
            models.Index(fields=['node', 'status']),
        ]

    def __str__(self):
        return f"Replica {self.container_name} for {self.service}"
