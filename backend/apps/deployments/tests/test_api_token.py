# pylint: disable=invalid-name
"""Tests for API Token authentication (model + DRF backend + views)."""

import hashlib
import hmac
import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from ..models.api_token import APIToken
from ..models import (
    Service,  # type: ignore[attr-defined]    # models.py hub no longer re-exports; class lives in models_core.py.
)

User = get_user_model()


class APITokenModelTests(TestCase):
    """Test the APIToken model directly."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="tokenuser", email="token@test.com", password="testpass123"
        )

    def test_create_token_returns_instance_and_raw(self):
        token_obj, raw = APIToken.create_token(self.user, "My CLI")
        self.assertIsNotNone(token_obj.id)
        self.assertTrue(raw.startswith("smsly_"))
        self.assertEqual(len(raw), 54)  # "smsly_" (6) + 48 hex chars

    def test_prefix_stored(self):
        token_obj, raw = APIToken.create_token(self.user)
        self.assertEqual(token_obj.prefix, raw[:12])

    def test_verify_valid_token(self):
        _, raw = APIToken.create_token(self.user, "Test")
        user, token = APIToken.verify(raw)
        self.assertEqual(user.id, self.user.id)
        self.assertTrue(token.is_active)

    def test_verify_invalid_token_raises(self):
        from rest_framework.exceptions import AuthenticationFailed

        with self.assertRaises(AuthenticationFailed):
            APIToken.verify("smsly_deadbeef1234567890abcdef1234567890abcdef12345678")

    def test_verify_inactive_token_raises(self):
        from rest_framework.exceptions import AuthenticationFailed

        token_obj, raw = APIToken.create_token(self.user)
        token_obj.is_active = False
        token_obj.save()
        with self.assertRaises(AuthenticationFailed):
            APIToken.verify(raw)

    def test_token_hash_is_not_raw(self):
        token_obj, raw = APIToken.create_token(self.user)
        self.assertNotEqual(token_obj.token_hash, raw)

    def test_last_used_updated_on_verify(self):
        _, raw = APIToken.create_token(self.user)
        _, token = APIToken.verify(raw)
        self.assertIsNotNone(token.last_used_at)


class APITokenViewTests(TestCase):
    """Test the token management API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="viewuser", email="view@test.com", password="testpass123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_token(self):
        resp = self.client.post("/api/v1/tokens/create/", {"name": "Test CLI"})
        self.assertEqual(resp.status_code, 201)
        self.assertIn("token", resp.data)
        self.assertTrue(resp.data["token"].startswith("smsly_"))

    def test_list_tokens(self):
        APIToken.create_token(self.user, "A")
        APIToken.create_token(self.user, "B")
        resp = self.client.get("/api/v1/tokens/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["tokens"]), 2)

    def test_revoke_token(self):
        token_obj, _ = APIToken.create_token(self.user, "Revoke Me")
        resp = self.client.delete(f"/api/v1/tokens/{token_obj.id}/revoke/")
        self.assertEqual(resp.status_code, 200)
        token_obj.refresh_from_db()
        self.assertFalse(token_obj.is_active)

    def test_revoke_other_users_token_404(self):
        other = User.objects.create_user(
            username="other", email="other@test.com", password="pass"
        )
        token_obj, _ = APIToken.create_token(other, "Other")
        resp = self.client.delete(f"/api/v1/tokens/{token_obj.id}/revoke/")
        self.assertEqual(resp.status_code, 404)

    def test_bearer_auth_in_request(self):
        _, raw = APIToken.create_token(self.user, "Bearer Test")
        # Use a fresh client without force_authenticate
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
        resp = client.get("/api/v1/services/")
        self.assertEqual(resp.status_code, 200)

    def test_unauthenticated_denied(self):
        client = APIClient()
        resp = client.get("/api/v1/tokens/")
        self.assertIn(resp.status_code, [401, 403])


class RemoteSyncHMACAuthenticationTests(TestCase):
    """Regression tests for node-to-node HMAC remote sync authentication."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="remote-owner",
            email="remote-owner@test.com",
            password="testpass123",
        )
        User.objects.create_superuser(
            username="admin",
            email="admin@test.com",
            password="testpass123",
        )
        Service.objects.create(
            owner=self.owner,
            name="hmac-api",
            repository_url="https://github.com/example/hmac-api.git",
        )

    @override_settings(GATEWAY_SECRET="remote-sync-secret")
    def test_remote_sync_hmac_can_list_services_without_api_token(self):
        path = "/api/v1/services/?search=hmac-api"
        timestamp = str(int(time.time()))
        nonce = "remote-sync-test-nonce"
        body_hash = hashlib.sha256(b"").hexdigest()
        # SECURITY (Batch G): nonce is mandatory and bound into the
        # signed payload.
        payload = f"GET|{path}|{timestamp}|{nonce}|{body_hash}"
        signature = hmac.new(
            b"remote-sync-secret",
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        client = APIClient()
        response = client.generic(
            "GET",
            path,
            HTTP_X_SMSLY_REMOTE_SYNC="1",
            HTTP_X_GATEWAY_SIGNATURE_V2=signature,
            HTTP_X_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_REQUEST_NONCE=nonce,
        )

        self.assertEqual(response.status_code, 200)
