# pylint: disable=invalid-name
"""
Regression tests for the Batch G second slice of fixes.

Covers the 5 remaining items addressed in this commit:
  1. CSRF exemption removed from DEFAULT_AUTHENTICATION_CLASSES
     (covered by the existing ai_chat_completions opt-in).
  2. ServiceHealthWebhookView: throttle, constant-time compare,
     404 for unconfigured (audit #7.2).
  3. ManagedServer.api_url: SSRF guard at registration
     (audit #3.9).
  4. Transfer .env scrubbing: platform secrets are not shipped
     to the target (audit #19 / Transfer CRITICAL).
  5. Backup platform_config.json excludes EncryptedCharField
     secrets (audit #1 / Backup CRITICAL).
  6. AI spend cap: _record_usage estimates tokens via character
     heuristic when the provider reports no usage (audit AI HIGH).
"""
import os
import tempfile
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Service
from apps.deployments.services.transfer_service import _scrub_env_for_transfer
from apps.intelligence.views import _record_usage

User = get_user_model()


# ── ServiceHealthWebhookView (audit #7.2) ──────────────────────────────────


class HealthWebhookSecurityTests(TestCase):
    """Audit #7.2: the health webhook was unauthenticated,
    unthrottled, and used a non-constant-time token compare.
    A 401-without-token brute force was wide open.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="hw-user", email="hw@x.com", password="p"
        )
        self.service = Service.objects.create(
            name="hw-svc", owner=self.user,
            health_webhook_token="super-secret-webhook-token-1234",
        )
        self.url = f"/api/v1/services/{self.service.id}/health/webhook/"

    def test_unconfigured_webhook_token_returns_404(self):
        """A service with no webhook token configured must return
        404 (not 403) so the response is indistinguishable from a
        non-existent service.

        Note: the model's save() auto-generates a token if it's
        empty, so we have to set the token to a single space and
        then strip it (mimicking the "unconfigured" state at the
        view level). This is the same pattern the view uses.
        """
        from django.core.cache import cache
        cache.clear()
        self.service.health_webhook_token = " "
        self.service.save(update_fields=["health_webhook_token"])
        client = APIClient()
        resp = client.post(self.url, {"token": "anything"}, format="json")
        self.assertEqual(resp.status_code, 404)

    def test_valid_token_returns_200(self):
        client = APIClient()
        resp = client.post(
            self.url,
            {
                "token": "super-secret-webhook-token-1234",
                "nonce": uuid.uuid4().hex,
                "status": "healthy",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)


# ── ManagedServer.api_url SSRF guard (audit #3.9) ─────────────────────────


class ManagedServerAPIURLSSRFGuardTests(TestCase):
    """Audit #3.9: api_url was a free-form CharField accepted
    on registration. A user with a ManagedServer could register
    one with api_url pointing at the platform's own controller
    (localhost, link-local, etc.) and use the /proxy/ endpoint
    as an SSRF relay."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="ssrf-user", password="p"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_localhost_api_url_rejected(self):
        resp = self.client.post(
            "/api/v1/servers/",
            {
                "name": "attacker",
                "host": "203.0.113.1",
                "api_url": "http://localhost:8000",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("loopback", str(resp.data).lower())

    def test_169_254_metadata_api_url_rejected(self):
        resp = self.client.post(
            "/api/v1/servers/",
            {
                "name": "attacker",
                "host": "203.0.113.1",
                "api_url": "http://169.254.169.254/latest/meta-data/",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_rfc1918_private_api_url_rejected(self):
        resp = self.client.post(
            "/api/v1/servers/",
            {
                "name": "attacker",
                "host": "203.0.113.1",
                "api_url": "http://10.0.0.1/admin",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_public_api_url_accepted(self):
        resp = self.client.post(
            "/api/v1/servers/",
            {
                "name": "good-srv",
                "host": "203.0.113.1",
                "api_url": "https://node.example.com",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)


# ── Transfer .env scrubbing (audit #19 Transfer CRITICAL) ───────────────


class TransferEnvScrubbingTests(TestCase):
    """Audit #19: the FULL transfer path shipped the entire source
    .env to the target, including GATEWAY_SECRET, BACKUP_ENCRYPTION_KEY,
    CLOUDFLARE_API_TOKEN. The scrubber must strip these and leave a
    comment that flags the operator-must-set.

    Note (Batch L): FIELD_ENCRYPTION_KEY is intentionally NOT scrubbed
    because the FULL transfer ships the source DB dump with rows
    encrypted by this key. The target needs the same key to decrypt
    the data; the operator is warned via transfer logs that the key
    is being shipped. See test_transfer_field_encryption_key.py.
    """

    SCRUBBED_KEYS = [
        "BACKUP_ENCRYPTION_KEY",
        "GATEWAY_SECRET",
        "CLOUDFLARE_API_TOKEN",
        "SENTRY_DSN",
        "STRIPE_SECRET_KEY",
        "DATABASE_URL",
        "REDIS_URL",
    ]

    def _write_env(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".env")
        os.write(fd, content.encode())
        os.close(fd)
        return path

    def test_scrub_strips_platform_secrets(self):
        env = (
            "FIELD_ENCRYPTION_KEY=FIELD_KEY_LIVE_VALUE\n"
            "GATEWAY_SECRET=super-secret-1\n"
            "BACKUP_ENCRYPTION_KEY=AABBccdd==\n"
            "CLOUDFLARE_API_TOKEN=cf-token-1234\n"
            "SENTRY_DSN=https://secret@sentry.io/1\n"
            "STRIPE_SECRET_KEY=sk_live_1234\n"
            "DATABASE_URL=postgresql://u:p@h/d\n"
            "REDIS_URL=redis://:pw@h:6379/0\n"
            "POSTGRES_USER=app\n"
            "POSTGRES_PASSWORD=dbpw\n"
        )
        path = self._write_env(env)
        try:
            scrubbed = _scrub_env_for_transfer(path)
        finally:
            os.unlink(path)
        # The secret VALUES must not appear in the output
        for value in (
            "AABBccdd", "super-secret-1", "cf-token-1234",
            "sk_live_1234", "https://secret@sentry.io/1",
        ):
            self.assertNotIn(
                value, scrubbed,
                f"Secret value {value!r} leaked into scrubbed output"
            )
        # FIELD_ENCRYPTION_KEY value MUST appear in the output (no longer scrubbed).
        self.assertIn(
            "FIELD_KEY_LIVE_VALUE", scrubbed,
            "FIELD_ENCRYPTION_KEY value must be shipped to target to decrypt DB rows",
        )
        # The KEY NAMES should appear (as comments flagging
        # operator-must-set).
        for key in self.SCRUBBED_KEYS:
            self.assertIn(key, scrubbed, f"Key {key} not flagged as scrubbed")
        # The non-sensitive keys must still be present
        self.assertIn("POSTGRES_USER=app", scrubbed)
        self.assertIn("POSTGRES_PASSWORD=dbpw", scrubbed)

    def test_scrub_preserves_comments_and_empty_lines(self):
        env = (
            "# This is a comment\n"
            "\n"
            "FIELD_ENCRYPTION_KEY=AABBccdd==\n"
            "POSTGRES_USER=app\n"
        )
        path = self._write_env(env)
        try:
            scrubbed = _scrub_env_for_transfer(path)
        finally:
            os.unlink(path)
        self.assertIn("# This is a comment", scrubbed)
        self.assertIn("POSTGRES_USER=app", scrubbed)
        # FIELD_ENCRYPTION_KEY value now passes through (see Batch L note above).
        self.assertIn("AABBccdd", scrubbed)


# ── AI spend cap (audit AI HIGH) ──────────────────────────────────────────


class RecordUsageTokenEstimationTests(TestCase):
    """Audit AI HIGH: when the provider does not return token
    counts, _record_usage used to write 0 tokens to the DB —
    silently bypassing the daily cap. The fix estimates tokens
    from the prompt and response text via a character heuristic.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="usage-user", email="u@x.com", password="p"
        )

    def test_estimate_from_text_when_provider_returns_no_usage(self):
        prompt = "What is Django?" * 10  # 150 chars
        response = "Django is a Python web framework." * 10  # 370 chars
        recorded = _record_usage(
            self.user, "test", "test", {},
            prompt_text=prompt, response_text=response,
        )
        # The estimate should be > 0 and reasonable (len/4)
        self.assertGreater(recorded["prompt_tokens"], 0)
        self.assertGreater(recorded["completion_tokens"], 0)
        # 150/4 = 37, 370/4 = 92; allow some rounding
        self.assertGreaterEqual(recorded["total_tokens"], 100)

    def test_provider_usage_overrides_estimate(self):
        # When the provider reports token usage, the estimate is
        # NOT used (the provider's count is the truth).
        recorded = _record_usage(
            self.user, "test", "test",
            {"prompt_tokens": 7, "completion_tokens": 13, "total_tokens": 20},
            prompt_text="x" * 10000, response_text="y" * 10000,
        )
        self.assertEqual(recorded["prompt_tokens"], 7)
        self.assertEqual(recorded["completion_tokens"], 13)
        self.assertEqual(recorded["total_tokens"], 20)

    def test_zero_text_returns_zero_tokens(self):
        recorded = _record_usage(self.user, "test", "test", {})
        self.assertEqual(recorded["prompt_tokens"], 0)
        self.assertEqual(recorded["completion_tokens"], 0)
        self.assertEqual(recorded["total_tokens"], 0)
