"""Tests for enforced two-factor authentication on the login flows.

Before this change, 2FA enrollment existed (TOTP devices) but NOTHING
ever gated token issuance: POST /api/v1/auth/login/ returned a full
DRF token on username+password alone. The two_factor_login endpoint
was unreachable in practice (missing session state + no token
issuance), and the recovery flow only created a Django session, which
the token-based SPA ignores.
"""
import time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.authtoken.models import Token

User = get_user_model()


def _valid_code(device: TOTPDevice) -> str:
    """Compute the currently-valid TOTP code for a device."""
    return str(totp(device.bin_key)).zfill(6)


class _AuthClient(Client):
    """Test client that preserves the session across requests (needed
    for the 2fa_user_id pending handshake)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("SERVER_NAME", "grid.smsly.cloud")
        super().__init__(*args, **kwargs)


# Monotonic counter so every test method gets its own throttle ident
# (HTTP_CF_CONNECTING_IP). Throttle buckets are per-IP; sharing one
# ident across methods would let one test's requests exhaust another
# test's budget (login scope is 10/min) and produce order-dependent
# 429s. This keeps each test hermetic without touching shared cache.
_test_ip_counter = [0]


def _unique_client(**kwargs):
    _test_ip_counter[0] += 1
    kwargs.setdefault(
        "HTTP_CF_CONNECTING_IP", f"10.9.0.{_test_ip_counter[0]}"
    )
    return _AuthClient(**kwargs)


class Login2FAGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mfa-user", email="mfa@example.com", password="secret123",
        )
        self.client = _unique_client()

    def _login(self, **extra):
        return self.client.post(
            "/api/v1/auth/login/",
            data={"username": "mfa-user", "password": "secret123", **extra},
            content_type="application/json",
        )

    def test_plain_login_issues_token_without_2fa(self):
        resp = self._login()
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        self.assertIn("key", resp.json())

    def test_login_gated_when_2fa_enrolled(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        Token.objects.filter(user=self.user).delete()

        resp = self._login()
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        data = resp.json()
        self.assertTrue(data.get("requires_2fa"), data)
        self.assertNotIn("key", data)
        # No DRF token must be minted before TOTP
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        # ... and the session cookie must not carry auth either
        self.assertNotIn("key", resp.cookies.__str__() if hasattr(resp, "cookies") else "")

    def test_full_2fa_flow_issues_token_and_cookie(self):
        device = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        Token.objects.filter(user=self.user).delete()

        step1 = self._login()
        self.assertTrue(step1.json().get("requires_2fa"))

        # Wrong code -> 401, no token
        bad = self.client.post(
            "/api/v1/auth/2fa/login/",
            data={"token": "000000"},
            content_type="application/json",
        )
        self.assertEqual(bad.status_code, 401)
        self.assertFalse(Token.objects.filter(user=self.user).exists())

        # Right code -> 200 with key + HttpOnly auth cookie
        good = self.client.post(
            "/api/v1/auth/2fa/login/",
            data={"token": _valid_code(device)},
            content_type="application/json",
        )
        self.assertEqual(good.status_code, 200, good.content[:300])
        self.assertIn("key", good.json())
        self.assertTrue(Token.objects.filter(user=self.user).exists())

        cookie_names = list(good.cookies.keys())
        self.assertTrue(
            any("auth_token" in name for name in cookie_names),
            f"auth cookie missing, got: {cookie_names}",
        )

        # The issued token authenticates API calls
        api = _AuthClient(
            HTTP_AUTHORIZATION=f"Token {good.json()['key']}"
        )
        me = api.get("/api/v1/auth/user/")
        self.assertEqual(me.status_code, 200)

    def test_2fa_login_without_pending_handshake_rejected(self):
        # Anonymous user with no pending session state
        fresh = _unique_client()
        resp = fresh.post(
            "/api/v1/auth/2fa/login/",
            data={"token": "123456"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_2fa_attempt_cap_locks_session(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        self._login()
        # A distinct ident per attempt isolates the per-SESSION cap
        # from the per-IP throttle (both are 10): every request must
        # reach the view so the 11th hits the session lockout, not
        # the IP bucket.
        for i in range(10):
            r = self.client.post(
                "/api/v1/auth/2fa/login/",
                data={"token": "000000"},
                content_type="application/json",
                HTTP_CF_CONNECTING_IP=f"10.31.0.{i + 1}",
            )
            self.assertEqual(r.status_code, 401)
        locked = self.client.post(
            "/api/v1/auth/2fa/login/",
            data={"token": "000000"},
            content_type="application/json",
            HTTP_CF_CONNECTING_IP="10.31.0.99",
        )
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json().get("code"), "2fa_locked")

    def test_session_token_exchange_gated_for_2fa_user(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        # Simulate an OAuth-established Django session
        self.client.force_login(self.user)
        resp = self.client.post(
            "/api/v1/auth/session-token/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.assertTrue(resp.json().get("requires_2fa"), resp.json())
        self.assertNotIn("token", resp.json())

    def test_session_token_exchange_normal_user(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            "/api/v1/auth/session-token/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("token", resp.json())


class RecoveryIssuesTokenTests(TestCase):
    """recovery_phrase_verify must return a usable API credential."""

    def _load_config(self, phrase_words, salt="testsalt"):
        # NOTE: do NOT go through PlatformConfig.load() here — it can
        # return a cached/ghost instance (pk set, no DB row in the test
        # DB), and save(update_fields=...) then fails with "did not
        # affect any rows". Write the singleton row directly.
        from apps.core.services.recovery import hash_recovery_phrase
        from apps.deployments.models.core import PlatformConfig
        import json

        h = hash_recovery_phrase(phrase_words, salt)
        PlatformConfig.objects.update_or_create(
            pk=1,
            defaults={"recovery_phrase_hash": json.dumps({"hash": h, "salt": salt})},
        )

    def test_recovery_returns_key_and_cookie(self):
        from apps.core.services.recovery import generate_recovery_phrase

        words = generate_recovery_phrase()
        self._load_config(words)
        admin = User.objects.create_superuser(
            username="rec-admin", email="rec@example.com", password="x",
        )

        client = _unique_client()
        resp = client.post(
            "/api/v1/auth/recovery/verify/",
            data={"username": "rec-admin", "phrase": " ".join(words)},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("key", data, "recovery must issue an API token")
        self.assertTrue(Token.objects.filter(user=admin).exists())

        cookie_names = list(resp.cookies.keys())
        self.assertTrue(
            any("auth_token" in name for name in cookie_names),
            f"auth cookie missing, got: {cookie_names}",
        )
