"""Service metrics model — tracks CPU, memory, network, and disk usage."""
import uuid
from django.db import models
from .models import Service, TimeStampedModel


class ServiceMetric(TimeStampedModel):
    """Real service metrics collected from Docker stats."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='metrics')

    # CPU
    cpu_usage = models.DecimalField(
        max_digits=10, decimal_places=4,
        help_text="CPU cores used")
    cpu_limit = models.DecimalField(
        max_digits=10, decimal_places=4, default=1.0,
        help_text="CPU cores allocated")

    # Memory
    memory_usage = models.IntegerField(help_text="Memory used in MB")
    memory_limit = models.IntegerField(
        default=512, help_text="Memory allocated in MB")

    # Network I/O
    network_rx_bytes = models.BigIntegerField(
        default=0, help_text="Network bytes received")
    network_tx_bytes = models.BigIntegerField(
        default=0, help_text="Network bytes sent")

    # Disk I/O
    disk_read_bytes = models.BigIntegerField(
        default=0, help_text="Disk bytes read")
    disk_write_bytes = models.BigIntegerField(
        default=0, help_text="Disk bytes written")

    # Timestamp
    timestamp = models.DateTimeField(db_index=True)

    class Meta:
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
