"""Election regressions exercise real HMAC bytes, DRF permissions and DB roles."""
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

from django.utils import timezone

from django.db import connection
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory

from apps.core.middleware.security import SecurityMiddleware
from apps.core.views.election import heartbeat_receive, vote_request
from apps.deployments.models.election import ClusterState
from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer
from apps.deployments.services.election_service import ElectionService


@override_settings(DEBUG=False, SMSLY_DISABLE_SIGNATURE_CHECK=False)
class ElectionReadinessTests(TransactionTestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user(username="election-readiness")
        self.mesh = MeshNetwork.objects.create(name="election-readiness")
        self.local = ManagedServer.objects.create(owner=owner, name="local", host="192.0.2.1", gateway_secret="local-test-secret")
        self.remote = ManagedServer.objects.create(owner=owner, name="remote", host="192.0.2.2", gateway_secret="remote-test-secret")
        for server, address, local in [(self.local, "10.100.0.1", True), (self.remote, "10.100.0.2", False)]:
            WireGuardPeer.objects.create(mesh=self.mesh, server=server, wg_address=address, is_local=local, public_key=address, private_key="test")
        self.cluster = ClusterState.objects.create(mesh=self.mesh, term=1)
        self.factory = APIRequestFactory()
        # Avoid compatibility role-file writes and external Redis publishing.
        self.enterContext(patch("builtins.open"))
        self.enterContext(patch("apps.deployments.services.heartbeat_bus.publish_heartbeat"))

    def post(self, kind, payload, secret="remote-test-secret", timestamp=None, signed=True, suffix=b"", nonce=None):
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        nonce = nonce or uuid.uuid4().hex
        signature = hmac.new(secret.encode(), f"{payload.get('sender_wg_address', '')}|{timestamp}|{nonce}|{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()
        headers = {"HTTP_X_ELECTION_SIGNATURE": signature, "HTTP_X_REQUEST_TIMESTAMP": timestamp, "HTTP_X_ELECTION_NONCE": nonce} if signed else {}
        request = self.factory.post(f"/api/v1/internal/{kind}/", body + suffix, content_type="application/json", **headers)
        view = heartbeat_receive if kind == "heartbeat" else vote_request
        return SecurityMiddleware(view)(request)

    def payload(self, kind="heartbeat", term=2):
        return {"term": term, "sender_wg_address": "10.100.0.2", ("leader_wg_address" if kind == "heartbeat" else "candidate_wg_address"): "10.100.0.2"}

    def test_raw_signed_heartbeat_reaches_drf_and_persists_follower(self):
        self.cluster.local_role = "LEADER"
        self.cluster.save()
        response = self.post("heartbeat", self.payload())
        self.assertEqual(response.status_code, 200)
        self.cluster.refresh_from_db()
        self.assertEqual((self.cluster.term, self.cluster.local_role, self.cluster.leader_wg_address), (2, "FOLLOWER", "10.100.0.2"))

    def test_replayed_signed_heartbeat_cannot_refresh_timeout(self):
        nonce = uuid.uuid4().hex
        self.assertEqual(self.post("heartbeat", self.payload(), nonce=nonce).status_code, 200)
        self.cluster.refresh_from_db()
        heartbeat = self.cluster.last_heartbeat
        self.assertIn(self.post("heartbeat", self.payload(), nonce=nonce).status_code, (401, 403))
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.last_heartbeat, heartbeat)

    def test_replay_cache_failure_rejects_signed_request(self):
        with patch("django.core.cache.cache.add", side_effect=RuntimeError("unavailable")):
            self.assertIn(self.post("vote", self.payload("vote")).status_code, (401, 403))
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.term, 1)

    def test_raw_signed_vote_is_granted_only_once(self):
        response = self.post("vote", self.payload("vote"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["vote_granted"])
        self.assertFalse(self.post("vote", self.payload("vote")).data["vote_granted"])

    def test_gateway_exemption_does_not_cover_other_internal_routes(self):
        request = self.factory.post("/api/v1/internal/heartbeat/extra/", {}, format="json")
        with override_settings(DEBUG=False, SMSLY_DISABLE_SIGNATURE_CHECK=False):
            response = SecurityMiddleware(lambda request: None)(request)
        self.assertEqual(response.status_code, 403)

    def test_inactive_peer_cannot_authenticate(self):
        self.mesh.peers.filter(server=self.remote).update(is_active=False)
        self.assertIn(self.post("vote", self.payload("vote")).status_code, (401, 403))
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.term, 1)

    def test_unsigned_wrong_secret_stale_and_tampered_requests_cannot_mutate(self):
        for options in [{"signed": False}, {"secret": "wrong"}, {"timestamp": 1}, {"suffix": b" "}]:
            with self.subTest(options=options):
                self.assertIn(self.post("heartbeat", self.payload(), **options).status_code, (401, 403))
                self.cluster.refresh_from_db()
                self.assertEqual(self.cluster.term, 1)

    def test_peer_cannot_impersonate_another_candidate_or_leader(self):
        for kind, field in [("heartbeat", "leader_wg_address"), ("vote", "candidate_wg_address")]:
            payload = self.payload(kind)
            payload[field] = "10.100.0.1"
            self.assertIn(self.post(kind, payload).status_code, (400, 401, 403))
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.term, 1)

    def test_signed_invalid_term_is_bad_request(self):
        for value in ["bad", True, -1, 1.5]:
            self.assertEqual(self.post("vote", self.payload("vote", value)).status_code, 400)

    def test_promoted_local_leader_sends_exact_signed_wire_bytes(self):
        sent = []
        def transport(session, prepared, **kwargs):
            self.assertFalse(connection.in_atomic_block, "Peer I/O must run after the election transaction commits")
            body = prepared.body
            if isinstance(body, str):
                body = body.encode()
            data = json.loads(body)
            timestamp = prepared.headers["X-Request-Timestamp"]
            expected = hmac.new(b"local-test-secret", f"10.100.0.1|{timestamp}|{prepared.headers['X-Election-Nonce']}|{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()
            self.assertEqual(prepared.headers["X-Election-Signature"], expected)
            self.assertEqual(data["sender_wg_address"], "10.100.0.1")
            sent.append(prepared)
            response = requests.Response()
            response.status_code = 200
            response._content = b'{"vote_granted":true,"term":2}'
            return response
        with patch("requests.sessions.Session.send", autospec=True, side_effect=transport):
            self.assertTrue(ElectionService.start_election(self.cluster))
        self.assertEqual(len(sent), 2)  # vote, then the new leader's heartbeat
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.local_role, "LEADER")

    def test_partitioned_two_node_cluster_cannot_invent_quorum(self):
        with patch("requests.sessions.Session.send", side_effect=requests.ConnectionError("partition")), patch("apps.deployments.services.heartbeat_bus.get_latest_heartbeats", return_value=[]):
            self.assertFalse(ElectionService.start_election(self.cluster))
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.local_role, "FOLLOWER")

    def test_expired_candidate_can_recover_after_worker_loss(self):
        self.cluster.local_role = "CANDIDATE"
        self.cluster.last_heartbeat = timezone.now() - timedelta(minutes=1)
        self.cluster.save()
        with patch("requests.sessions.Session.send", side_effect=requests.ConnectionError("partition")):
            self.assertFalse(ElectionService.start_election(self.cluster))
        self.cluster.refresh_from_db()
        self.assertEqual((self.cluster.term, self.cluster.local_role), (2, "FOLLOWER"))

    def test_active_candidate_does_not_start_duplicate_campaign(self):
        self.cluster.local_role = "CANDIDATE"
        self.cluster.last_heartbeat = timezone.now()
        self.cluster.save()
        self.assertFalse(ElectionService.start_election(self.cluster))
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.term, 1)

    def test_inflight_election_cannot_overwrite_newer_heartbeat(self):
        def transport(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            ElectionService.receive_heartbeat(self.cluster, 8, "10.100.0.2")
            response = requests.Response()
            response.status_code = 200
            response._content = b'{"vote_granted":true,"term":2}'
            return response
        with patch("requests.sessions.Session.send", side_effect=transport):
            self.assertFalse(ElectionService.start_election(self.cluster))
        self.cluster.refresh_from_db()
        self.assertEqual((self.cluster.term, self.cluster.local_role), (8, "FOLLOWER"))

    def test_higher_term_vote_reply_prevents_promotion(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"vote_granted":false,"term":9}'
        with patch("requests.sessions.Session.send", return_value=response), patch("apps.deployments.services.heartbeat_bus.get_latest_heartbeats", return_value=[]):
            self.assertFalse(ElectionService.start_election(self.cluster))
        self.cluster.refresh_from_db()
        self.assertEqual((self.cluster.term, self.cluster.local_role), (9, "FOLLOWER"))

    def test_higher_term_heartbeat_reply_demotes_local_leader(self):
        self.cluster.local_role = "LEADER"
        self.cluster.leader_wg_address = "10.100.0.1"
        self.cluster.leader = self.local
        self.cluster.save()
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"accepted":true,"term":9}'
        with patch("requests.sessions.Session.send", return_value=response):
            ElectionService.send_heartbeat(self.cluster)
        self.cluster.refresh_from_db()
        self.assertEqual((self.cluster.term, self.cluster.local_role), (9, "FOLLOWER"))

    def test_role_transitions_persist_without_file_fallback(self):
        with patch("requests.sessions.Session.send", side_effect=requests.ConnectionError("offline test")):
            ElectionService.promote_to_leader(self.cluster)
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.local_role, "LEADER")
        ElectionService.demote_to_follower(self.cluster, 5)
        self.cluster.refresh_from_db()
        self.assertEqual(self.cluster.local_role, "FOLLOWER")

    def test_heartbeat_task_uses_each_mesh_role(self):
        from apps.deployments.tasks.infra.tasks_election import heartbeat_task
        other = MeshNetwork.objects.create(name="other")
        ClusterState.objects.create(mesh=other, local_role="LEADER")
        seen = []
        def record(cluster):
            seen.append((str(cluster.mesh_id), cluster.local_role))
        with patch.object(ElectionService, "send_heartbeat", side_effect=record), patch.object(ElectionService, "check_leader_timeout", return_value=False):
            heartbeat_task()
        self.assertEqual(seen, [(str(other.pk), "LEADER")])
