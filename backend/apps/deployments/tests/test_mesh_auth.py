import hashlib
import hmac
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import BaseThrottle

from apps.deployments.models import ManagedServer
from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.views.attestation import attestation_verify


# Mock the throttle to avoid Redis connection issues
class MockThrottle(BaseThrottle):
    def allow_request(self, request, view):
        return True

@pytest.mark.django_db(transaction=True)
class TestMeshAuth(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        User = get_user_model()

        self.username = "mesh_auth_" + str(uuid.uuid4())[:8]
        self.user = User.objects.create_user(username=self.username, password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="mesh-server",
            host="203.0.113.20",
            api_url="https://mesh.example.com",
            api_token="valid",
            gateway_secret="my-mesh-secret",
        )
        self.mesh = MeshNetwork.objects.create(
            name="default_mesh",
        )
        self.peer = WireGuardPeer.objects.create(
            server=self.server,
            mesh=self.mesh,
            wg_address="10.8.0.5",
            public_key="pubkey123",
        )

    def tearDown(self):
        self.peer.delete()
        self.mesh.delete()
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.views.attestation.cache")
    def test_attestation_verify_rejects_invalid_signature_with_401(self, mock_cache):
        mock_cache.get.return_value = "10.8.0.5"

        request = self.factory.post("/api/v1/internal/attest/verify/", data={
            "challenge": "my-nonce",
            "signature": "bad-signature",
            "sender_wg_address": "10.8.0.5",
        }, format="json")
        force_authenticate(request, user=self.user)

        with patch('rest_framework.views.APIView.get_throttles', return_value=[]):
            response = attestation_verify(request)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Signature mismatch", response.data.get("error", ""))

    @patch("apps.deployments.views.attestation.cache")
    def test_attestation_verify_accepts_valid_signature(self, mock_cache):
        mock_cache.get.return_value = "10.8.0.5"

        nonce = "my-nonce"
        valid_signature = hmac.new(
            b"my-mesh-secret",
            nonce.encode(),
            hashlib.sha256,
        ).hexdigest()

        request = self.factory.post("/api/v1/internal/attest/verify/", data={
            "challenge": nonce,
            "signature": valid_signature,
            "sender_wg_address": "10.8.0.5",
        }, format="json")
        force_authenticate(request, user=self.user)

        with patch('rest_framework.views.APIView.get_throttles', return_value=[]):
            response = attestation_verify(request)
            self.assertEqual(response.status_code, 200)
