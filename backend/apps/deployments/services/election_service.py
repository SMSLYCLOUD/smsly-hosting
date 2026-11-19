"""
Leader election service.

Simplified Raft-like protocol for 2-5 server clusters.
- Leader sends heartbeats to all followers every N seconds.
- Followers start election if no heartbeat received within timeout.
- Candidate with majority votes becomes the new leader.
- Old leader demotes to follower on discovering higher term.
"""

import hashlib
import hmac as hmac_mod
import json
import logging
import time
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _election_body(payload: dict) -> bytes:
    """Serialize the exact bytes signed and transmitted by peers."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _build_election_hmac_headers(
    payload: dict,
    wg_address: str,
    gateway_secret: str,
) -> dict:
    """
    Build HMAC V2 headers for election protocol messages.

    The signature covers (sender_wg|timestamp|nonce|sha256(body)).
    """
    timestamp = str(int(time.time()))
    body_bytes = _election_body(payload)
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    nonce = uuid.uuid4().hex
    sign_input = f"{wg_address}|{timestamp}|{nonce}|{body_hash}"
    signature = hmac_mod.new(
        gateway_secret.encode(),
        sign_input.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Election-Signature": signature,
        "X-Request-Timestamp": timestamp,
        "X-Election-Nonce": nonce,
    }


class ElectionService:
    """Manage leader election and heartbeats across the server cluster."""

    # ── Heartbeat ────────────────────────────────────────────────────────

    @classmethod
    def send_heartbeat(cls, cluster):
        """
        Leader sends heartbeat to all followers via WireGuard mesh.

        Called periodically by Celery beat. Only executes if this server
        is the current leader.
        """
        import requests

        from apps.deployments.models.election import HeartbeatLog

        if cluster.local_role != "LEADER":
            logger.debug("Local server is not leader — skipping heartbeat")
            return

        # Get all peers except the local one
        mesh = cluster.mesh
        if not mesh:
            return

        local_peer = mesh.peers.filter(is_local=True).first()
        if not local_peer:
            return

        remote_peers = mesh.peers.filter(is_active=True).exclude(is_local=True)

        # Get local gateway_secret for HMAC signing
        local_server = local_peer.server if local_peer else None
        local_gateway_secret = str(
            getattr(local_server, "gateway_secret", "") or ""
        ).strip()
        if not local_gateway_secret:
            local_gateway_secret = str(getattr(settings, "GATEWAY_SECRET", ""))
        local_wg = local_peer.wg_address if local_peer else ""

        for peer in remote_peers:
            start = time.monotonic()
            success = False
            error_msg = ""
            latency = None

            try:
                # Send heartbeat via WireGuard IP to internal API
                url = f"http://{peer.wg_address}:8000/api/v1/internal/heartbeat/"
                payload = {
                    "term": cluster.term,
                    "leader_wg_address": local_wg,
                    "sender_wg_address": local_wg,
                }
                hmac_headers = _build_election_hmac_headers(
                    payload, local_wg, local_gateway_secret,
                )
                resp = requests.post(
                    url,
                    data=_election_body(payload),
                    headers=hmac_headers,
                    timeout=5,
                )
                latency = (time.monotonic() - start) * 1000
                success = resp.status_code == 200
                if success:
                    reply_term = resp.json().get("term")
                    if type(reply_term) is int and reply_term > cluster.term:
                        cls.demote_to_follower(cluster, reply_term)
                        return
                if not success:
                    error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as e:
                latency = (time.monotonic() - start) * 1000
                error_msg = str(e)[:500]

            # Log heartbeat
            HeartbeatLog.objects.create(
                cluster=cluster,
                source_server=None,  # local
                target_server=peer.server,
                term=cluster.term,
                latency_ms=latency,
                success=success,
                error_message=error_msg,
            )

            if success:
                logger.debug(
                    f"Heartbeat to {peer.wg_address} OK ({latency:.1f}ms)"
                )
            else:
                logger.warning(
                    f"Heartbeat to {peer.wg_address} FAILED: {error_msg}"
                )

        # Update last heartbeat timestamp
        cluster.last_heartbeat = timezone.now()
        cluster.save(update_fields=["last_heartbeat"])

    # ── Heartbeat Receive ────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def receive_heartbeat(cls, cluster, term, leader_wg_address):
        """
        Process a received heartbeat from the leader.

        Returns True if heartbeat is accepted (valid term),
        False if rejected (stale term).
        """
        cluster = type(cluster).objects.select_for_update().get(pk=cluster.pk)
        if term < cluster.term:
            # Stale heartbeat from an old leader — reject
            logger.info(
                f"Rejected stale heartbeat (term {term} < {cluster.term})"
            )
            return False

        if term > cluster.term:
            # New leader with higher term — accept and update
            logger.info(
                f"Accepted new leader (term {term} > {cluster.term})"
            )
            cluster.term = term

        cluster.last_heartbeat = timezone.now()
        cluster.leader_wg_address = leader_wg_address
        leader_peer = cluster.mesh.peers.filter(wg_address=leader_wg_address, is_active=True).first() if cluster.mesh else None
        cluster.leader = leader_peer.server if leader_peer else None
        cluster.state = "STABLE"
        cluster.save(update_fields=[
            "term", "last_heartbeat", "leader", "leader_wg_address", "state",
        ])

        # Ensure local server is in FOLLOWER role
        cls._set_local_role("FOLLOWER", cluster)

        return True

    # ── Election ─────────────────────────────────────────────────────────

    @classmethod
    def check_leader_timeout(cls, cluster):
        """
        Check if the leader's heartbeat has timed out.

        Called periodically by followers. If timeout is detected,
        starts an election.

        Returns True if election was started.
        """
        if not cluster.last_heartbeat:
            peer_count = cls._get_peer_count(cluster)
            if peer_count > 0:
                return cls.start_election(cluster)
            # No mesh or zero peers — single-server mode, promote self
            cls.promote_to_leader(cluster)
            return True

        elapsed_ms = (
            timezone.now() - cluster.last_heartbeat
        ).total_seconds() * 1000

        if elapsed_ms > cluster.election_timeout_ms:
            logger.warning(
                f"Leader timeout detected! "
                f"Elapsed: {elapsed_ms:.0f}ms > {cluster.election_timeout_ms}ms"
            )
            return cls.start_election(cluster)

        return False

    @classmethod
    def start_election(cls, cluster):
        """Commit candidacy, collect votes without locks, then fence promotion."""
        import requests

        from apps.deployments.models.election import ElectionVote

        with transaction.atomic():
            cluster = type(cluster).objects.select_for_update().get(pk=cluster.pk)
            # A concurrent worker must not start another campaign for this node.
            if cluster.local_role == "CANDIDATE" and cluster.last_heartbeat:
                elapsed_ms = (timezone.now() - cluster.last_heartbeat).total_seconds() * 1000
                if elapsed_ms <= cluster.election_timeout_ms:
                    return False
            cluster.term += 1
            cluster.state = "ELECTION"
            cluster.last_heartbeat = timezone.now()
            cluster.save(update_fields=["term", "state", "last_heartbeat"])
            cls._set_local_role("CANDIDATE", cluster)
            new_term = cluster.term
            ElectionVote.objects.create(
                cluster=cluster, term=new_term, voter_server=None,
                candidate_server=None, candidate_is_local=True,
            )
            mesh = cluster.mesh
            local_peer = mesh.peers.filter(is_local=True, is_active=True).first() if mesh else None
            remote_peers = list(mesh.peers.filter(is_active=True).exclude(is_local=True)) if mesh else []
            total_servers = len(remote_peers) + 1
            majority = (total_servers // 2) + 1
            local_server = local_peer.server if local_peer else None
            local_gateway_secret = str(getattr(local_server, "gateway_secret", "") or "").strip()
            if not local_gateway_secret:
                local_gateway_secret = str(getattr(settings, "GATEWAY_SECRET", ""))
            local_wg = local_peer.wg_address if local_peer else ""

        # No transaction/row lock spans network I/O: competing candidates must
        # be able to process each other's requests while votes are outstanding.
        votes_for_self = 1
        for peer in remote_peers:
            try:
                payload = {
                    "term": new_term,
                    "candidate_wg_address": local_wg,
                    "sender_wg_address": local_wg,
                }
                resp = requests.post(
                    f"http://{peer.wg_address}:8000/api/v1/internal/vote/",
                    data=_election_body(payload),
                    headers=_build_election_hmac_headers(payload, local_wg, local_gateway_secret),
                    timeout=5,
                )
                if resp.status_code != 200:
                    logger.warning("Vote denied by %s: HTTP %s", peer.wg_address, resp.status_code)
                    continue
                data = resp.json()
                with transaction.atomic():
                    current = type(cluster).objects.select_for_update().get(pk=cluster.pk)
                    reply_term = data.get("term")
                    if type(reply_term) is int and reply_term > current.term:
                        cls.demote_to_follower(current, reply_term)
                        return False
                    if current.term != new_term or current.local_role != "CANDIDATE":
                        return False
                    if data.get("vote_granted") is True:
                        votes_for_self += 1
                        ElectionVote.objects.create(
                            cluster=current, term=new_term, voter_server=peer.server,
                            candidate_server=None, candidate_is_local=True,
                        )
            except Exception as exc:
                logger.warning("Failed to request vote from %s: %s", peer.wg_address, exc)

        with transaction.atomic():
            current = type(cluster).objects.select_for_update().get(pk=cluster.pk)
            if current.term != new_term or current.local_role != "CANDIDATE":
                return False
            if votes_for_self < majority:
                cls._set_local_role("FOLLOWER", current)
                current.state = "STABLE"
                current.save(update_fields=["state"])
                return False
            # Persist promotion while holding the same lock as the term/role
            # comparison; the announcement is sent only after commit.
            cls.promote_to_leader(current, announce=False)
        cls.send_heartbeat(current)
        return True

    # ── Promotion / Demotion ─────────────────────────────────────────────

    @classmethod
    def promote_to_leader(cls, cluster, announce=True):
        """
        Promote the local server to leader.

        Updates cluster state and notifies all peers.
        """
        mesh = cluster.mesh
        local_peer = mesh.peers.filter(is_local=True).first() if mesh else None

        cluster.state = "STABLE"
        cluster.leader = local_peer.server if local_peer else None
        cluster.leader_wg_address = local_peer.wg_address if local_peer else None
        cluster.last_heartbeat = timezone.now()
        cluster.save()

        cls._set_local_role("LEADER", cluster)

        logger.info(
            f"Promoted to LEADER for term {cluster.term} "
            f"(wg: {local_peer.wg_address if local_peer else 'N/A'})"
        )

        # Election finalization persists under lock, announces after commit.
        if announce:
            cls.send_heartbeat(cluster)

    @classmethod
    @transaction.atomic
    def demote_to_follower(cls, cluster, new_leader_term):
        """
        Demote from leader to follower when a higher term is discovered.

        This handles split-brain resolution.
        """
        cluster = type(cluster).objects.select_for_update().get(pk=cluster.pk)
        if new_leader_term <= cluster.term:
            return  # Ignore stale demotion requests

        cluster.term = new_leader_term
        cluster.state = "STABLE"
        cluster.leader = None  # Will be set by next heartbeat
        cluster.leader_wg_address = None
        cluster.save(update_fields=["term", "state", "leader", "leader_wg_address"])

        cls._set_local_role("FOLLOWER", cluster)

        logger.info(
            f"Demoted to FOLLOWER (new term: {new_leader_term})"
        )

    # ── Vote Handling ────────────────────────────────────────────────────

    @classmethod
    @transaction.atomic
    def handle_vote_request(cls, cluster, term, candidate_wg_address):
        """
        Handle a vote request from a candidate.

        Returns True if vote is granted.

        Rules:
        - Grant vote if candidate's term >= our term
        - Only vote once per term
        - Reset election timeout when granting vote
        """
        from apps.deployments.models.election import ElectionVote

        # Serialize the local vote; nullable voter_server does not enforce
        # uniqueness for local votes at the database constraint level.
        cluster = type(cluster).objects.select_for_update().get(pk=cluster.pk)
        if term < cluster.term:
            return False  # Reject — we're in a newer term

        # Check if already voted in this term
        already_voted = ElectionVote.objects.filter(
            cluster=cluster,
            term=term,
            voter_server=None,  # local server's vote
        ).exists()

        if already_voted:
            return False  # Already voted

        # If candidate has higher term, update ours
        if term > cluster.term:
            cluster.term = term
            cluster.save(update_fields=["term"])
            cls._set_local_role("FOLLOWER", cluster)

        # Grant vote
        ElectionVote.objects.create(
            cluster=cluster,
            term=term,
            voter_server=None,
            candidate_server=None,
            candidate_is_local=False,
        )

        # Reset election timeout
        cluster.last_heartbeat = timezone.now()
        cluster.save(update_fields=["last_heartbeat"])

        logger.info(f"Granted vote to {candidate_wg_address} for term {term}")
        return True

    # ── Cluster Initialization ───────────────────────────────────────────

    @classmethod
    def get_or_create_cluster(cls, mesh=None):
        """
        Get or create the ClusterState for a mesh.

        If the cluster doesn't exist and this is the first server,
        auto-elect self as leader.
        """
        from apps.deployments.models.election import ClusterState

        cluster, created = ClusterState.objects.get_or_create(
            mesh=mesh,
            defaults={
                "state": "STABLE",
                "term": 1,
            },
        )

        if created:
            logger.info(f"Created new cluster state (mesh: {mesh})")
            # Auto-elect self as leader if we're the only server
            if cls._get_peer_count(cluster) <= 1:
                cls.promote_to_leader(cluster)

        return cluster

    # ── Helpers ──────────────────────────────────────────────────────────

    @classmethod
    def _set_local_role(cls, role: str, cluster=None):
        """Set the local server's role in the database."""
        if cluster:
            cluster.local_role = role
            cluster.save(update_fields=["local_role"])
        # Also write to /tmp for backward compatibility
        role_file = "/tmp/.smsly_cluster_role"
        try:
            with open(role_file, "w") as f:
                f.write(role)
        except OSError as exc:
            logger.debug("Failed to write cluster role file %s: %s", role_file, exc)
        logger.info(f"Local server role: {role}")

    @staticmethod
    def _get_peer_count(cluster):
        """Get the total number of active peers in the cluster's mesh."""
        if not cluster.mesh:
            return 0
        return cluster.mesh.peers.filter(is_active=True).count()

    @classmethod
    def cleanup_old_heartbeats(cls, cluster, keep_count=500):
        """Remove old heartbeat logs to prevent table bloat."""
        from apps.deployments.models.election import HeartbeatLog

        total = HeartbeatLog.objects.filter(cluster=cluster).count()
        if total > keep_count:
            cutoff = HeartbeatLog.objects.filter(
                cluster=cluster,
            ).order_by("-timestamp").values_list(
                "timestamp", flat=True,
            )[keep_count]
            deleted, _ = HeartbeatLog.objects.filter(
                cluster=cluster, timestamp__lt=cutoff,
            ).delete()
            logger.info(f"Cleaned up {deleted} old heartbeat logs")

    @classmethod
    def get_cluster_status(cls, cluster):
        """Get a summary of the cluster's current state."""
        from apps.deployments.models.election import HeartbeatLog

        recent_heartbeats = HeartbeatLog.objects.filter(
            cluster=cluster,
        ).order_by("-timestamp")[:10]

        return {
            "term": cluster.term,
            "state": cluster.state,
            "leader_wg_address": cluster.leader_wg_address,
            "last_heartbeat": cluster.last_heartbeat.isoformat() if cluster.last_heartbeat else None,
            "heartbeat_interval_ms": cluster.heartbeat_interval_ms,
            "election_timeout_ms": cluster.election_timeout_ms,
            "peer_count": cls._get_peer_count(cluster),
            "min_quorum": cluster.min_quorum,
            "recent_heartbeats": [
                {
                    "target": hb.target_server.name if hb.target_server else "local",
                    "success": hb.success,
                    "latency_ms": hb.latency_ms,
                    "timestamp": hb.timestamp.isoformat(),
                }
                for hb in recent_heartbeats
            ],
        }
