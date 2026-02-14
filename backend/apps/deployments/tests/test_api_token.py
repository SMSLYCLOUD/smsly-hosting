"""Tests for API Token authentication (model + DRF backend + views)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from ..api_token_auth import APIToken

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
