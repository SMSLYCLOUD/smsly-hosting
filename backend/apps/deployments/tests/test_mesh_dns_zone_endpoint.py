"""Node CoreDNS replica zone endpoint (HMAC, no session).

GET /api/v1/servers/<uuid>/mesh-dns-zone/ serves the mesh zone files
a node needs for its local CoreDNS replica. Auth mirrors
agent-ready/agent-heartbeat: per-server gateway_secret HMAC.
"""
import hashlib
import time

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer
from apps.deployments.services.agent_registrar_auth import compute_agent_hmac


def _hmac_headers(secret, method, path, body=b""):
    import secrets as _secrets
    ts = str(int(time.time()))
    nonce = f"test-nonce-{_secrets.token_hex(4)}"
    sig = compute_agent_hmac(secret, method, path, ts, nonce, body)
    return {
        "HTTP_X_GATEWAY_SIGNATURE_V2": sig,
        "HTTP_X_REQUEST_TIMESTAMP": ts,
        "HTTP_X_REQUEST_NONCE": nonce,
    }


class MeshDnsZoneEndpointTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="zone_admin", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="zone-node-1",
            host="10.0.0.60",
            node_number=7,
            gateway_secret="zone-test-secret-1234567890abcdef",
        )
        self.mesh = MeshNetwork.objects.create(
            name="default",
            subnet="10.100.0.0/24",
            listen_port=51820,
            interface_name="wg0",
            is_active=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh,
            server=None,
            private_key="priv",
            public_key="masterpub",
            wg_address="10.100.0.1",
            endpoint="",
            allowed_ips="10.100.0.1/32",
            is_active=True,
            is_local=True,
        )
        WireGuardPeer.objects.create(
            mesh=self.mesh,
            server=self.server,
            private_key="priv2",
            public_key="nodepub",
            wg_address="10.100.0.7",
            endpoint="10.0.0.60:51820",
            allowed_ips="10.100.0.7/32",
            is_active=True,
            is_local=False,
        )
        self.url = f"/api/v1/servers/{self.server.id}/mesh-dns-zone/"
        self.client = APIClient()

    def test_valid_hmac_returns_zone(self):
        resp = self.client.get(
            self.url, **_hmac_headers("zone-test-secret-1234567890abcdef", "GET", self.url)
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("mesh.internal", data["domain"])
        self.assertGreaterEqual(data["records"], 3)
        self.assertIn("10.100.0.1 master.mesh.internal", data["hosts"])
        self.assertIn("10.100.0.7 grid7.mesh.internal", data["hosts"])
        self.assertIn("hosts ", data["corefile"])
        self.assertIn("mesh.hosts", data["corefile"])

    def test_bad_signature_rejected(self):
        headers = _hmac_headers("wrong-secret", "GET", self.url)
        resp = self.client.get(self.url, **headers)
        self.assertEqual(resp.status_code, 401)

    def test_missing_headers_rejected(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_unknown_server_404(self):
        import uuid
        url = f"/api/v1/servers/{uuid.uuid4()}/mesh-dns-zone/"
        resp = self.client.get(
            url, **_hmac_headers("zone-test-secret-1234567890abcdef", "GET", url)
        )
        self.assertEqual(resp.status_code, 404)
