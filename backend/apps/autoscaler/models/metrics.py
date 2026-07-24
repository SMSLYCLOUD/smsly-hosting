"""Service metrics model — tracks CPU, memory, network, and disk usage."""
import uuid

from django.db import models

from apps.deployments.models.core import Service, TimeStampedModel


class ServiceMetric(TimeStampedModel):
    """Real service metrics collected from Docker stats."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='metrics')

    # CPU
    cpu_usage = models.DecimalField(  # type: ignore[var-annotated]
        max_digits=10, decimal_places=4,
        help_text="CPU cores used")
    cpu_limit = models.DecimalField(  # type: ignore[var-annotated]
        max_digits=10, decimal_places=4, default=1.0,
        help_text="CPU cores allocated")

    # Memory
    memory_usage = models.IntegerField(help_text="Memory used in MB")  # type: ignore[var-annotated]
    memory_limit = models.IntegerField(  # type: ignore[var-annotated]
        default=512, help_text="Memory allocated in MB")

    # Network I/O
    network_rx_bytes = models.BigIntegerField(  # type: ignore[var-annotated]
        default=0, help_text="Network bytes received")
    network_tx_bytes = models.BigIntegerField(  # type: ignore[var-annotated]
        default=0, help_text="Network bytes sent")

    # Disk I/O
    disk_read_bytes = models.BigIntegerField(  # type: ignore[var-annotated]
        default=0, help_text="Disk bytes read")
    disk_write_bytes = models.BigIntegerField(  # type: ignore[var-annotated]
        default=0, help_text="Disk bytes written")

    # Timestamp
    timestamp = models.DateTimeField(db_index=True)  # type: ignore[var-annotated]

    class Meta:
        db_table = 'deployments_servicemetric'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['service', '-timestamp']),
        ]

    @property
    def cpu_percent(self):
        """CPU utilization percentage."""
        if self.cpu_limit and self.cpu_limit > 0:
            return float(self.cpu_usage / self.cpu_limit * 100)
        return 0

    @property
    def memory_percent(self):
        """Memory utilization percentage."""
        if self.memory_limit and self.memory_limit > 0:
            return self.memory_usage / self.memory_limit * 100
        return 0
