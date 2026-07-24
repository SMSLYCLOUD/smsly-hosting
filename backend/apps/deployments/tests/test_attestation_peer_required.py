"""
Regression tests for Issue 47.

``attestation_verify`` must reject callers that do not present an
``X-SMSLY-Remote-Sync-Peer-WG`` header matching a known
``ManagedServer.wg_address``. The header check is the first thing the
endpoint does so an attacker with a stolen ``GATEWAY_SECRET`` cannot
probe whether arbitrary wg_addresses are valid peers.
"""
import hashlib
import hmac
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework.throttling import BaseThrottle

from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer
from apps.deployments.models.servers import ManagedServer
from apps.deployments.views.attestation import (
    CHALLENGE_CACHE_MAX_ENTRIES,
    CHALLENGE_CACHE_PREFIX,
    attestation_verify,
)

User = get_user_model()


class _NoThrottle(BaseThrottle):
    def allow_request(self, request, view):
        return True


VERIFY_URL = "/api/v1/internal/attest/verify/"
CHALLENGE_URL = "/api/v1/internal/attest/challenge/"


def _sign(secret: str, nonce: str) -> str:
    return hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "attest-peer-test",
        }
    }
)
class AttestationPeerHeaderRequiredTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="peer-test", password="p",
        )
        self.peer_wg = "10.55.0.10"
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="peer-server",
            host="198.51.100.10",
            wg_address=self.peer_wg,
            gateway_secret="server-secret",
        )
        # The verify endpoint also resolves the peer via WireGuardPeer
        # (select_related("server")). WireGuardPeer is the legacy mesh
        # table; ManagedServer is the new one. Both are kept in sync
        # by the platform's join flow, so the test must populate both.
        self.mesh = MeshNetwork.objects.create(name="default_mesh")
        self.wg_peer = WireGuardPeer.objects.create(
            server=self.server,
            mesh=self.mesh,
            wg_address=self.peer_wg,
            public_key="pubkey123",
            is_active=True,
        )
        self.nonce = "nonce-" + uuid.uuid4().hex
        cache.set(
            f"{CHALLENGE_CACHE_PREFIX}{self.nonce}",
            self.peer_wg,
            timeout=120,
        )

    def tearDown(self):
        cache.clear()
        self.wg_peer.delete()
        self.mesh.delete()
        self.server.delete()
        self.user.delete()

    def _post(self, *, headers=None, data=None, factory=None):
        factory = factory or APIRequestFactory()
        request = factory.post(
            VERIFY_URL,
            data=data or {
                "challenge": self.nonce,
                "signature": _sign("server-secret", self.nonce),
                "sender_wg_address": self.peer_wg,
            },
            format="json",
        )
        if headers:
            for key, value in headers.items():
                request.META[key] = value
        force_authenticate(request, user=self.user)
        with patch(
            "rest_framework.views.APIView.get_throttles",
            return_value=[_NoThrottle()],
        ):
            return attestation_verify(request)

    def test_missing_peer_header_returns_401(self):
        resp = self._post(headers={})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("X-SMSLY-Remote-Sync-Peer-WG", resp.data.get("error", ""))

    def test_unknown_peer_wg_returns_403(self):
        resp = self._post(
            headers={"HTTP_X_SMSLY_REMOTE_SYNC_PEER_WG": "10.99.99.99"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Unknown peer", resp.data.get("error", ""))

    def test_header_must_match_sender_wg_address(self):
        resp = self._post(
            headers={"HTTP_X_SMSLY_REMOTE_SYNC_PEER_WG": self.peer_wg},
            data={
                "challenge": self.nonce,
                "signature": _sign("server-secret", self.nonce),
                "sender_wg_address": "10.55.0.99",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_valid_peer_header_and_signature_returns_200(self):
        resp = self._post(
            headers={"HTTP_X_SMSLY_REMOTE_SYNC_PEER_WG": self.peer_wg},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("verified"))

    def test_client_request_rejects_without_header(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        resp = client.post(
            VERIFY_URL,
            data={
                "challenge": self.nonce,
                "signature": _sign("server-secret", self.nonce),
                "sender_wg_address": self.peer_wg,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 401)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "attest-cap-test",
        }
    }
)
class AttestationChallengeCapTests(TestCase):
    """The challenge endpoint must cap the total number of
    outstanding nonces in the cache."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="cap-tester", password="p",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_challenge_cap_returns_503_when_full(self):
        from apps.deployments.views import attestation as views_attestation

        # Pre-fill the counter past the cap.
        cache.set(
            views_attestation.CHALLENGE_COUNT_CACHE_KEY,
            CHALLENGE_CACHE_MAX_ENTRIES,
            timeout=240,
        )
        with patch(
            "rest_framework.views.APIView.get_throttles",
            return_value=[_NoThrottle()],
        ):
            resp = self.client.post(
                CHALLENGE_URL,
                data={"target_wg_address": "10.0.0.1"},
                format="json",
            )
        self.assertEqual(resp.status_code, 503)
        self.assertIn("cache is full", resp.data.get("error", ""))
