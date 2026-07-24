"""
Regression tests for the election mesh-ambiguity fix (Issue 38).

Covers:
  1. heartbeat_receive only processes heartbeats against the mesh
     that actually contains the sending peer.
  2. vote_request rejects the request when no active mesh contains
     the candidate peer.
  3. vote_request routes correctly when multiple active meshes exist.
"""
import hashlib
import hmac as hmac_mod
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer

User = get_user_model()


def _sign(body_bytes: bytes, sender_wg: str, timestamp: str, gateway_secret: str) -> str:
    payload = f"{sender_wg}|{timestamp}|{hashlib.sha256(body_bytes).hexdigest()}"
    return hmac_mod.new(
        gateway_secret.encode(), payload.encode(), hashlib.sha256,
    ).hexdigest()


def _always_valid_hmac(*args, **kwargs):
    """Bypass the pre-existing ``request.data`` / ``request.body``
    ordering bug in ``_verify_election_hmac`` for these tests."""
    return (True, "")


class MeshAmbiguityTests(TestCase):
    def setUp(self):
        self.test_user = User.objects.create_user(
            username="mesh-test-user", password="p",
        )
        self.master_secret = "test-master-secret-1234567890"
        self.peer_a_secret = "peer-a-secret-abcdefghij"
        self.peer_b_secret = "peer-b-secret-klmnopqrst"

        self.master = ManagedServer.objects.create(
            owner=self.test_user,
            name="master", host="203.0.113.1",
            wg_address="10.100.0.1",
            api_url="https://203.0.113.1",
            gateway_secret=self.master_secret,
        )
        self.peer_a = ManagedServer.objects.create(
            owner=self.test_user,
            name="peer-a", host="203.0.113.2",
            wg_address="10.100.0.2",
            api_url="https://203.0.113.2",
            gateway_secret=self.peer_a_secret,
        )
        self.peer_b = ManagedServer.objects.create(
            owner=self.test_user,
            name="peer-b", host="203.0.113.3",
            wg_address="10.100.0.3",
            api_url="https://203.0.113.3",
            gateway_secret=self.peer_b_secret,
        )

        # Two active meshes. Peer A is only in mesh_alpha; peer B is
        # only in mesh_beta. The OLD code iterated all active meshes
        # and called receive_heartbeat against every cluster, so peer
        # A's heartbeat would be misrouted to mesh_beta.
        self.mesh_alpha = MeshNetwork.objects.create(
            name="alpha", is_active=True,
        )
        self.mesh_beta = MeshNetwork.objects.create(
            name="beta", is_active=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh_alpha, server=self.master,
            private_key="x", public_key="pub-master-a",
            wg_address="10.100.0.1", is_active=True, is_local=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh_alpha, server=self.peer_a,
            private_key="y", public_key="pub-peer-a-a",
            wg_address="10.100.0.2", is_active=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh_beta, server=self.master,
            private_key="z", public_key="pub-master-b",
            wg_address="10.100.0.1", is_active=True, is_local=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh_beta, server=self.peer_b,
            private_key="w", public_key="pub-peer-b-b",
            wg_address="10.100.0.3", is_active=True,
        )

    def _post_signed(self, url, data):
        import json
        client = APIClient()
        client.force_authenticate(user=self.test_user)
        body = json.dumps(data).encode()
        return client.post(url, data=body, content_type="application/json")

    def test_heartbeat_routes_only_to_containing_mesh(self):
        url = "/api/v1/internal/heartbeat/"
        with patch(
            "apps.core.views.election._verify_election_hmac",
            side_effect=_always_valid_hmac,
        ), patch(
            "apps.deployments.services.election_service."
            "ElectionService.receive_heartbeat"
        ) as mock_recv:
            mock_recv.return_value = True
            resp = self._post_signed(
                url,
                data={"term": 1, "leader_wg_address": "10.100.0.2"},
            )
        self.assertEqual(resp.status_code, 200)
        # Peer A is only in mesh_alpha; heartbeat must hit only that mesh.
        called_meshes = [
            call.args[0].mesh
            for call in mock_recv.call_args_list
        ]
        self.assertEqual(called_meshes, [self.mesh_alpha])

    def test_vote_request_rejects_unknown_peer(self):
        # Add a third peer that exists in the peer table (so HMAC passes)
        # but is in a *disabled* mesh, so the application must reject the
        # vote because no active mesh contains this candidate.
        inactive_mesh = MeshNetwork.objects.create(
            name="inactive-mesh", is_active=False,
        )
        unknown = ManagedServer.objects.create(
            owner=self.test_user,
            name="unknown-peer", host="203.0.113.99",
            wg_address="10.100.0.99",
            api_url="https://203.0.113.99",
            gateway_secret="unknown-secret-1234567890",
        )
        WireGuardPeer.objects.create(
            mesh=inactive_mesh, server=unknown,
            private_key="k", public_key="pub-unknown",
            wg_address="10.100.0.99", is_active=True,
        )

        url = "/api/v1/internal/vote/"
        with patch(
            "apps.core.views.election._verify_election_hmac",
            side_effect=_always_valid_hmac,
        ):
            resp = self._post_signed(
                url,
                data={"term": 1, "candidate_wg_address": "10.100.0.99"},
            )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("No active mesh", str(resp.data))

    def test_vote_request_routes_to_containing_mesh(self):
        url = "/api/v1/internal/vote/"
        with patch(
            "apps.core.views.election._verify_election_hmac",
            side_effect=_always_valid_hmac,
        ), patch(
            "apps.deployments.services.election_service."
            "ElectionService.handle_vote_request"
        ) as mock_vote:
            mock_vote.return_value = True
            resp = self._post_signed(
                url,
                data={"term": 5, "candidate_wg_address": "10.100.0.3"},
            )
        self.assertEqual(resp.status_code, 200)
        called_meshes = [
            call.args[0].mesh
            for call in mock_vote.call_args_list
        ]
        self.assertEqual(called_meshes, [self.mesh_beta])
