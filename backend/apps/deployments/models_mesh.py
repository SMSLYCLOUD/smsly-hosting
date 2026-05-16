"""
WireGuard VPN Mesh models.

Track mesh networks and WireGuard peers for encrypted inter-server
communication.
"""

import uuid

from django.conf import settings
from django.db import models

from encrypted_model_fields.fields import EncryptedCharField


class MeshNetwork(models.Model):
    """
    A WireGuard mesh network connecting multiple CloudNeuron servers.
    Typically one per cluster.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        'deployments.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='mesh_networks',
        help_text="Project this mesh network belongs to (null = ungrouped)"
    )
    name = models.CharField(
        max_length=100, default="default",
        help_text="Mesh network name (e.g. 'production', 'staging')",
    )
    subnet = models.CharField(
        max_length=18, default="10.100.0.0/24",
        help_text="WireGuard subnet CIDR (e.g. 10.100.0.0/24)",
    )
    listen_port = models.IntegerField(
        default=51820,
        help_text="WireGuard listen port on each server",
    )
    interface_name = models.CharField(
        max_length=15, default="wg0",
        help_text="WireGuard interface name",
    )
    is_active = models.BooleanField(default=True)
    mesh_status = models.CharField(
        max_length=20,
        default="UNKNOWN",
        choices=[
            ("UNKNOWN", "Unknown"),
            ("DEPLOYING", "Deploying"),
            ("ACTIVE", "Active"),
            ("FAILED", "Failed"),
        ],
        help_text="Last known WireGuard deployment state.",
    )
    mesh_last_error = models.TextField(blank=True, default="")
    mesh_last_result = models.JSONField(default=dict, blank=True)
    mesh_last_deployed_at = models.DateTimeField(null=True, blank=True)
    replication_status = models.CharField(
        max_length=20,
        default="DISABLED",
        choices=[
            ("DISABLED", "Disabled"),
            ("DEPLOYING", "Deploying"),
            ("ACTIVE", "Active"),
            ("FAILED", "Failed"),
        ],
        help_text="Last known Patroni replication state.",
    )
    replication_last_error = models.TextField(blank=True, default="")
    replication_last_result = models.JSONField(default=dict, blank=True)
    replication_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mesh Network"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.subnet})"

    def next_available_ip(self):
        """Calculate the next available IP in the subnet."""
        import ipaddress
        network = ipaddress.IPv4Network(self.subnet, strict=False)
        used_ips = set(
            self.peers.values_list("wg_address", flat=True)
        )
        # Skip network address (.0) and broadcast (.255)
        for host in network.hosts():
            ip_str = str(host)
            if ip_str not in used_ips:
                return ip_str
        raise ValueError(f"No available IPs in subnet {self.subnet}")


class WireGuardPeer(models.Model):
    """
    A WireGuard peer representing one server in the mesh network.
    Each server has exactly one peer entry per mesh.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mesh = models.ForeignKey(
        MeshNetwork, on_delete=models.CASCADE, related_name="peers",
    )
    server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        related_name="wg_peers",
        null=True, blank=True,
        help_text="Linked ManagedServer (null for the local/primary server)",
    )

    # WireGuard keys
    private_key = EncryptedCharField(
        max_length=255,
        help_text="WireGuard private key (encrypted at rest)",
    )
    public_key = models.CharField(
        max_length=44,
        help_text="WireGuard public key (safe to share)",
    )

    # Network
    wg_address = models.GenericIPAddressField(
        protocol="IPv4",
        help_text="This peer's IP inside the WireGuard mesh (e.g. 10.100.0.1)",
    )
    endpoint = models.CharField(
        max_length=255, blank=True, default="",
        help_text="Public IP:port for this peer (e.g. 163.245.216.248:51820)",
    )
    allowed_ips = models.CharField(
        max_length=500, default="",
        help_text="Comma-separated CIDR ranges this peer can route",
    )

    # Status
    is_active = models.BooleanField(default=True)
    is_local = models.BooleanField(
        default=False,
        help_text="True if this peer represents the local (this) server",
    )
    last_handshake = models.DateTimeField(
        null=True, blank=True,
        help_text="Last WireGuard handshake time",
    )
    latency_ms = models.FloatField(
        null=True, blank=True,
        help_text="Last measured ping latency in ms",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "WireGuard Peer"
        ordering = ["wg_address"]
        unique_together = [("mesh", "server"), ("mesh", "wg_address")]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Automatically sync the wg_address to the linked ManagedServer
        if self.server and self.is_active and self.wg_address:
            # We use update() to avoid triggering the server's own save signals
            # which might cause recursion or unnecessary overhead.
            from .models_core import ManagedServer
            ManagedServer.objects.filter(id=self.server_id).update(
                wg_address=self.wg_address
            )

    def __str__(self):
        label = self.server.name if self.server else "local"
        return f"{label} ({self.wg_address})"
