"""Tests for the HMAC requirement on node-token-exchange.

The legacy username/password path on ``node_token_exchange`` is
protected against brute-force by ``NodeTokenExchangeThrottle`` (5/min
per IP). An attacker with a botnet can still spray admin passwords at
5/min per IP — the throttle is bypassable in aggregate — and a
successful guess grants an ``APIToken`` with full superuser privileges
for inter-node sync.

The fix requires a valid ``X-GATEWAY-SIGNATURE-V2`` HMAC over
``method|path|ts|nonce|body_hash`` using ``GATEWAY_SECRET``. Only
already-provisioned nodes (which know the secret) can call this
endpoint, so brute-forcing the admin password is no longer viable at
any practical rate.
"""
import hashlib
import hmac
import json
import time

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

User = get_user_model()


TEST_GATEWAY_SECRET = "test-gateway-secret-for-node-exchange"


def _signed_post_kwargs(body_dict, *, secret=None, ts=None, nonce=None,
                        path="/api/v1/auth/node-token-exchange/"):
    secret = secret if secret is not None else TEST_GATEWAY_SECRET
    body = json.dumps(body_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ts = str(ts if ts is not None else int(time.time()))
    nonce = nonce if nonce is not None else f"nonce-{ts}-{hash(body)}"
    body_hash = hashlib.sha256(body).hexdigest()
    payload = f"POST|{path}|{ts}|{nonce}|{body_hash}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {
        "body": body,
        "headers": {
            "HTTP_X_GATEWAY_SIGNATURE_V2": signature,
            "HTTP_X_REQUEST_TIMESTAMP": ts,
            "HTTP_X_REQUEST_NONCE": nonce,
            "content_type": "application/json",
        },
    }


@override_settings(GATEWAY_SECRET=TEST_GATEWAY_SECRET)
class NodeTokenExchangeSafetyTests(TestCase):
    """Verify the HMAC requirement on the legacy node-token-exchange."""

    URL = "/api/v1/auth/node-token-exchange/"

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin", email="admin@x.com", password="STRONG_password_123!",
        )
        self.client = APIClient()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_rejects_without_hmac(self):
        """A request with valid admin creds but no HMAC must be 401."""
        response = self.client.post(
            self.URL,
            {"username": "admin", "password": "STRONG_password_123!"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        body = json.dumps(response.data) if hasattr(response, "data") else str(response.content)
        self.assertIn("hmac", body.lower())

    def test_rejects_with_wrong_hmac(self):
        """A bad signature is 401 even if the creds are correct."""
        body = json.dumps(
            {"username": "admin", "password": "STRONG_password_123!"},
            separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        ts = str(int(time.time()))
        nonce = "wrong-sig-nonce"
        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"POST|{self.URL}|{ts}|{nonce}|{body_hash}"
        bad_sig = hmac.new(b"wrong-secret", payload.encode(), hashlib.sha256).hexdigest()
        response = self.client.post(
            self.URL,
            data=body,
            content_type="application/json",
            HTTP_X_GATEWAY_SIGNATURE_V2=bad_sig,
            HTTP_X_REQUEST_TIMESTAMP=ts,
            HTTP_X_REQUEST_NONCE=nonce,
        )
        self.assertEqual(response.status_code, 401)
        body_text = json.dumps(response.data) if hasattr(response, "data") else str(response.content)
        self.assertIn("hmac", body_text.lower())

    def test_rejects_with_stale_timestamp(self):
        """An HMAC signed with a stale timestamp is rejected."""
        kwargs = _signed_post_kwargs(
            {"username": "admin", "password": "STRONG_password_123!"},
            ts=int(time.time()) - 3600,
            nonce="stale-ts-nonce",
        )
        response = self.client.post(
            self.URL, data=kwargs["body"], **kwargs["headers"],
        )
        self.assertIn(response.status_code, (401, 403))

    def test_rejects_with_replayed_nonce(self):
        """A nonce used twice is rejected on the second call."""
        kwargs = _signed_post_kwargs(
            {"username": "admin", "password": "STRONG_password_123!"},
            nonce="replay-nonce-1",
        )
        first = self.client.post(self.URL, data=kwargs["body"], **kwargs["headers"])
        self.assertIn(first.status_code, (200, 201))
        kwargs_replay = _signed_post_kwargs(
            {"username": "admin", "password": "STRONG_password_123!"},
            nonce="replay-nonce-1",
        )
        second = self.client.post(
            self.URL, data=kwargs_replay["body"], **kwargs_replay["headers"],
        )
        self.assertEqual(second.status_code, 401)

    def test_rejects_with_bad_password_even_with_hmac(self):
        """Valid HMAC but wrong password is still 401."""
        kwargs = _signed_post_kwargs(
            {"username": "admin", "password": "wrong"},
        )
        response = self.client.post(
            self.URL, data=kwargs["body"], **kwargs["headers"],
        )
        self.assertEqual(response.status_code, 401)

    def test_accepts_with_correct_hmac_and_creds(self):
        """Valid HMAC + valid creds → token issued."""
        kwargs = _signed_post_kwargs(
            {"username": "admin", "password": "STRONG_password_123!"},
            nonce="happy-path-nonce",
        )
        response = self.client.post(
            self.URL, data=kwargs["body"], **kwargs["headers"],
        )
        self.assertIn(response.status_code, (200, 201))
        self.assertIn("token", response.data)
        self.assertTrue(response.data["token"].startswith("smsly_"))

    def test_audits_failed_attempts(self):
        """A failed HMAC attempt writes a NODE_TOKEN_EXCHANGE_ATTEMPT
        audit log row with success=False."""
        from apps.deployments.models.audit import AuditLog
        AuditLog.objects.all().delete()
        response = self.client.post(
            self.URL,
            {"username": "admin", "password": "STRONG_password_123!"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        log_rows = list(
            AuditLog.objects.filter(action='NODE_TOKEN_EXCHANGE_ATTEMPT').order_by('-id')
        )
        self.assertTrue(log_rows, "expected a NODE_TOKEN_EXCHANGE_ATTEMPT audit log row")
        latest = log_rows[0]
        self.assertEqual(latest.metadata.get('success'), False)
        self.assertIn('invalid_or_missing_hmac', latest.metadata.get('reason', ''))

    def test_audits_successful_attempts(self):
        """A successful exchange writes an audit log row with success=True."""
        from apps.deployments.models.audit import AuditLog
        AuditLog.objects.all().delete()
        kwargs = _signed_post_kwargs(
            {"username": "admin", "password": "STRONG_password_123!"},
            nonce="audit-success-nonce",
        )
        response = self.client.post(
            self.URL, data=kwargs["body"], **kwargs["headers"],
        )
        self.assertIn(response.status_code, (200, 201))
        log_rows = list(
            AuditLog.objects.filter(action='NODE_TOKEN_EXCHANGE_ATTEMPT').order_by('-id')
        )
        self.assertTrue(log_rows)
        latest = log_rows[0]
        self.assertEqual(latest.metadata.get('success'), True)
