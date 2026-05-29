import uuid
from django.db import models
from django.utils import timezone

class FVMIPAllocation(models.Model):
    """
    Tracks centralized IP allocations for Firecracker MicroVMs across the fleet.
    Allocations are made from a supernet (e.g., 172.30.0.0/16).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node = models.ForeignKey(
        'deployments.ManagedServer',
        on_delete=models.CASCADE,
        related_name='fvm_allocations',
        help_text="The server node hosting the VM"
    )
    ip_address = models.GenericIPAddressField(
        protocol="IPv4",
        unique=True,
        help_text="Allocated IP address within the FVM supernet"
    )
    service = models.ForeignKey(
        'deployments.Service',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='fvm_allocations',
        help_text="Linked service, if this IP is for a deployed service"
    )
    vm_id = models.CharField(
        max_length=255,
        help_text="Unique identifier for the VM (e.g., addon container_name or service instance id)"
    )
    allocated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "FVM IP Allocation"
        ordering = ["ip_address"]

    def __str__(self):
        return f"{self.ip_address} -> {self.vm_id} on {self.node.name}"
