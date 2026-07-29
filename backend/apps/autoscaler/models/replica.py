import uuid

from django.db import models

from apps.deployments.models.core import ManagedServer, Service


class ServiceReplica(models.Model):
    """Tracks auto-scaled replicas spawned on remote nodes."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)  # type: ignore[var-annotated]
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='replicas')  # type: ignore[var-annotated]
    node = models.ForeignKey(  # type: ignore[var-annotated]
        ManagedServer, on_delete=models.SET_NULL, null=True,
        related_name='hosted_replicas',
        help_text='The remote node where this replica runs',
    )
    container_name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    container_id = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]

    status = models.CharField(  # type: ignore[var-annotated]
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

    metrics_snapshot = models.JSONField(default=dict, help_text='Last known CPU/mem from Prometheus')  # type: ignore[var-annotated]
    spawn_reason = models.CharField(max_length=500, blank=True, default='')  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    destroyed_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    class Meta:
        db_table = 'deployments_servicereplica'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['service', 'status']),
            models.Index(fields=['node', 'status']),
        ]

    def __str__(self):
        return f"Replica {self.container_name} for {self.service}"
