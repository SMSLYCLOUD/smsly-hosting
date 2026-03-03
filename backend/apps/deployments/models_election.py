"""
Leader election and cluster state models.

Track which server is the leader, heartbeat logs, and election history
for automatic failover in the CloudNeuron server fleet.
"""

import uuid

from django.conf import settings
from django.db import models


class ClusterState(models.Model):
    """
    Singleton-ish model tracking the current cluster leader and election state.
    One record per mesh/cluster.
    """

    class ElectionState(models.TextChoices):
        STABLE = "STABLE", "Stable (leader active)"
        ELECTION = "ELECTION", "Election in progress"
        SPLIT_BRAIN = "SPLIT_BRAIN", "Split brain detected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mesh = models.OneToOneField(
        "deployments.MeshNetwork",
        on_delete=models.CASCADE,
        related_name="cluster_state",
        null=True, blank=True,
        help_text="Associated mesh network (null = standalone cluster)",
    )
    leader = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="led_clusters",
        help_text="Current cluster leader",
    )
    leader_wg_address = models.GenericIPAddressField(
        protocol="IPv4", null=True, blank=True,
        help_text="Leader's WireGuard IP (for fast lookup)",
    )

    # Election state
    term = models.IntegerField(
        default=0,
        help_text="Current election term (monotonically increasing)",
    )
    state = models.CharField(
        max_length=20,
        choices=ElectionState.choices,
        default=ElectionState.STABLE,
    )
    last_heartbeat = models.DateTimeField(
        null=True, blank=True,
        help_text="Last heartbeat received from the leader",
    )

    # Configuration
    heartbeat_interval_ms = models.IntegerField(
        default=5000,
        help_text="How often the leader sends heartbeats (ms)",
    )
    election_timeout_ms = models.IntegerField(
        default=15000,
        help_text="How long followers wait before starting election (ms)",
    )
    min_quorum = models.IntegerField(
        default=2,
        help_text="Minimum servers needed to form quorum",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cluster State"
        verbose_name_plural = "Cluster States"

    def __str__(self):
        leader_name = self.leader.name if self.leader else "none"
        return f"Cluster (term={self.term}, leader={leader_name})"


class HeartbeatLog(models.Model):
    """
    Log of heartbeat checks between servers.
    Used for monitoring and debugging election issues.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cluster = models.ForeignKey(
        ClusterState, on_delete=models.CASCADE,
        related_name="heartbeats",
    )
    source_server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        related_name="sent_heartbeats",
        null=True, blank=True,
        help_text="Server that sent the heartbeat (null = local)",
    )
    target_server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        related_name="received_heartbeats",
        null=True, blank=True,
        help_text="Server that received the heartbeat (null = local)",
    )
    term = models.IntegerField(
        help_text="Election term when heartbeat was sent",
    )
    latency_ms = models.FloatField(
        null=True, blank=True,
        help_text="Round-trip latency in milliseconds",
    )
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Heartbeat Log"
        # Auto-cleanup: only keep last 1000 entries per cluster
        indexes = [
            models.Index(fields=["cluster", "-timestamp"]),
        ]

    def __str__(self):
        src = self.source_server.name if self.source_server else "local"
        tgt = self.target_server.name if self.target_server else "local"
        status = "OK" if self.success else "FAIL"
        return f"{src} → {tgt} [{status}] (term {self.term})"


class ElectionVote(models.Model):
    """
    Track votes during leader election.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cluster = models.ForeignKey(
        ClusterState, on_delete=models.CASCADE,
        related_name="votes",
    )
    term = models.IntegerField()
    voter_server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        related_name="cast_votes",
        null=True, blank=True,
        help_text="Server that cast the vote (null = local)",
    )
    candidate_server = models.ForeignKey(
        "deployments.ManagedServer",
        on_delete=models.CASCADE,
        related_name="received_votes",
        null=True, blank=True,
        help_text="Server that was voted for (null = local)",
    )
    candidate_is_local = models.BooleanField(
        default=False,
        help_text="True if voted for the local server",
    )
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-voted_at"]
        unique_together = [("cluster", "term", "voter_server")]
        verbose_name = "Election Vote"
