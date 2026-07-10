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

from django.conf import settings
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _build_election_hmac_headers(
    payload: dict,
    wg_address: str,
    gateway_secret: str,
) -> dict:
    """
    Build HMAC V2 headers for election protocol messages.

    The signature covers (sender_wg|timestamp|sha256(body)).
    """
    timestamp = str(int(time.time()))
    body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    sign_input = f"{wg_address}|{timestamp}|{body_hash}"
    signature = hmac_mod.new(
        gateway_secret.encode(),
        sign_input.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Election-Signature": signature,
        "X-Request-Timestamp": timestamp,
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

        from apps.deployments.models_election import HeartbeatLog

        if not cluster.leader:
            logger.warning("No leader set for cluster — skipping heartbeat")
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
                    json=payload,
                    headers=hmac_headers,
                    timeout=5,
                )
                latency = (time.monotonic() - start) * 1000
                success = resp.status_code == 200
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
    def receive_heartbeat(cls, cluster, term, leader_wg_address):
        """
        Process a received heartbeat from the leader.

        Returns True if heartbeat is accepted (valid term),
        False if rejected (stale term).
        """
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
        cluster.state = "STABLE"
        cluster.save(update_fields=[
            "term", "last_heartbeat", "leader_wg_address", "state",
        ])

        # Ensure local server is in FOLLOWER role
        cls._set_local_role("FOLLOWER")

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
            # No heartbeat ever received — start election if we have peers
            if cls._get_peer_count(cluster) > 0:
                return cls.start_election(cluster)
            return False

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
    @transaction.atomic
    def start_election(cls, cluster):
        """
        Start a new leader election.

        1. Increment term
        2. Vote for self
        3. Request votes from all peers
        4. If majority, promote to leader
        """
        import requests

        from apps.deployments.models_election import ElectionVote

        # Increment term and become candidate
        with transaction.atomic():
            cluster = cls.get_or_create_cluster(mesh=cluster.mesh)
            cluster = type(cluster).objects.select_for_update().get(pk=cluster.pk)
            cluster.term += 1
            cluster.state = "ELECTION"
            cluster.save(update_fields=["term", "state"])
            cls._set_local_role("CANDIDATE")

            new_term = cluster.term
            logger.info(f"Starting election for term {new_term}")

            # Vote for self
            ElectionVote.objects.create(
                cluster=cluster,
                term=new_term,
                voter_server=None,  # local
                candidate_server=None,  # local
                candidate_is_local=True,
            )
            votes_for_self = 1

            # Get peers and total count
            mesh = cluster.mesh
            if not mesh:
                # No mesh = auto-win (single server)
                cls.promote_to_leader(cluster)
                return True

            local_peer = mesh.peers.filter(is_local=True).first()
            remote_peers = list(
                mesh.peers.filter(is_active=True).exclude(is_local=True)
            )
            total_servers = len(remote_peers) + 1  # +1 for self
            majority = (total_servers // 2) + 1

            # Get local gateway_secret for HMAC signing
            local_server = local_peer.server if local_peer else None
            local_gateway_secret = str(
                getattr(local_server, "gateway_secret", "") or ""
            ).strip()
            if not local_gateway_secret:
                local_gateway_secret = str(getattr(settings, "GATEWAY_SECRET", ""))
            local_wg = local_peer.wg_address if local_peer else ""

            # Request votes from peers
            for peer in remote_peers:
                try:
                    url = f"http://{peer.wg_address}:8000/api/v1/internal/vote/"
                    payload = {
                        "term": new_term,
                        "candidate_wg_address": local_wg,
                        "sender_wg_address": local_wg,
                    }
                    hmac_headers = _build_election_hmac_headers(
                        payload, local_wg, local_gateway_secret,
                    )
                    resp = requests.post(
                        url,
                        json=payload,
                        headers=hmac_headers,
                        timeout=5,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("vote_granted"):
                            votes_for_self += 1
                            ElectionVote.objects.create(
                                cluster=cluster,
                                term=new_term,
                                voter_server=peer.server,
                                candidate_server=None,
                                candidate_is_local=True,
                            )
                    else:
                        logger.warning(f"Vote denied by {peer.wg_address}: HTTP {resp.status_code}")
                except Exception as e:
                    logger.warning(f"Failed to request vote from {peer.wg_address}: {e}")

            # --- SPECIAL CASE: Handle 2-node deadlock ---
            # In a 2-node cluster, if 1 node is down, Raft cannot reach a majority (2/2).
            # We allow a solo win ONLY after verifying the peer is genuinely unreachable.
            if votes_for_self < majority and total_servers == 2:
                peer = remote_peers[0]
                logger.info("2-node deadlock detected. Probing peer %s...", peer.wg_address)

                # Step 1: Probe the peer with an HTTP POST (timeout=3s)
                peer_reachable = False
                try:
                    probe_url = f"http://{peer.wg_address}:8000/api/v1/internal/heartbeat/"
                    probe_resp = requests.post(probe_url, json={"term": cluster.term, "leader_wg_address": local_wg}, timeout=3)
                    peer_reachable = probe_resp.status_code == 200
                except Exception:  # pylint: disable=broad-exception-caught
                    peer_reachable = False

                # Step 2: Also check recent heartbeat snapshots from Redis bus
                if not peer_reachable:
                    from apps.deployments.services.heartbeat_bus import get_latest_heartbeats
                    recent_hbs = get_latest_heartbeats()
                    now = time.time()
                    for hb in recent_hbs:
                        if (hb.get('wg_address') == peer.wg_address
                                and hb.get('status') == 'alive'
                                and (now - hb.get('ts', 0)) < 30):
                            peer_reachable = True
                            logger.info("Peer %s reachable via recent heartbeat bus snapshot", peer.wg_address)
                            break

                if not peer_reachable:
                    # Fencing: Verify no other node has already claimed leadership
                    # by checking if ANY heartbeat was received cluster-wide in the
                    # last 30s (the leader would have sent one).
                    from apps.deployments.services.heartbeat_bus import get_latest_heartbeats
                    any_recent_leader_hb = False
                    all_hbs = get_latest_heartbeats()
                    now = time.time()
                    for hb in all_hbs:
                        if (hb.get('wg_address') != peer.wg_address
                                and hb.get('status') == 'alive'
                                and (now - hb.get('ts', 0)) < 30):
                            any_recent_leader_hb = True
                            break
                    if not any_recent_leader_hb:
                        # Safe to promote — peer is down, no other leader active
                        votes_for_self += 1
                        logger.warning(
                            "Force-promoting in 2-node cluster: peer %s unreachable "
                            "and no leader heartbeat detected",
                            peer.wg_address,
                        )
                    else:
                        logger.warning(
                            "Abstaining from 2-node force-promote: another "
                            "leader heartbeat detected"
                        )
                else:
                    logger.info("Peer %s is reachable — cannot force promote", peer.wg_address)

            logger.info(
                f"Election term {new_term}: {votes_for_self}/{total_servers} votes "
                f"(need {majority} for majority)"
            )

            if votes_for_self >= majority:
                cls.promote_to_leader(cluster)
                return True
            else:
                # Didn't win — revert to follower
                cls._set_local_role("FOLLOWER")
                cluster.state = "STABLE"
                cluster.save(update_fields=["state"])
                logger.info("Election lost — reverting to follower")
                return False

    # ── Promotion / Demotion ─────────────────────────────────────────────

    @classmethod
    def promote_to_leader(cls, cluster):
        """
        Promote the local server to leader.

        Updates cluster state and notifies all peers.
        """
        mesh = cluster.mesh
        local_peer = mesh.peers.filter(is_local=True).first() if mesh else None

        cluster.state = "STABLE"
        cluster.leader = None  # local server (no ManagedServer record)
        cluster.leader_wg_address = local_peer.wg_address if local_peer else None
        cluster.last_heartbeat = timezone.now()
        cluster.save()

        cls._set_local_role("LEADER")

        logger.info(
            f"Promoted to LEADER for term {cluster.term} "
            f"(wg: {local_peer.wg_address if local_peer else 'N/A'})"
        )

        # Notify peers of new leadership via heartbeat
        cls.send_heartbeat(cluster)

    @classmethod
    def demote_to_follower(cls, cluster, new_leader_term):
        """
        Demote from leader to follower when a higher term is discovered.

        This handles split-brain resolution.
        """
        if new_leader_term <= cluster.term:
            return  # Ignore stale demotion requests

        cluster.term = new_leader_term
        cluster.state = "STABLE"
        cluster.leader = None  # Will be set by next heartbeat
        cluster.save(update_fields=["term", "state", "leader"])

        cls._set_local_role("FOLLOWER")

        logger.info(
            f"Demoted to FOLLOWER (new term: {new_leader_term})"
        )

    # ── Vote Handling ────────────────────────────────────────────────────

    @classmethod
    def handle_vote_request(cls, cluster, term, candidate_wg_address):
        """
        Handle a vote request from a candidate.

        Returns True if vote is granted.

        Rules:
        - Grant vote if candidate's term >= our term
        - Only vote once per term
        - Reset election timeout when granting vote
        """
        from apps.deployments.models_election import ElectionVote

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
            cls._set_local_role("FOLLOWER")

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
        from apps.deployments.models_election import ClusterState

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
        except Exception:
            pass
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
        from apps.deployments.models_election import HeartbeatLog

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
        from apps.deployments.models_election import HeartbeatLog

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
