# pylint: disable=invalid-name
"""
Regression tests for Issue 14 (ServiceHealthWebhookView hardening).

The webhook must, in addition to the per-service token, require:
  * an ``event`` field (only ``health_update`` or ``deploy_complete``);
  * an ``X-Webhook-Nonce`` header (or ``nonce`` body field) that is
    never reused within a 10-minute window;
  * HTTPS in production (``not settings.DEBUG and not IS_TESTING``);
  * an AuditLog entry on every successful or rejected invocation.
"""


from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments.models.audit import AuditLog
from apps.deployments.models.core import Service

User = get_user_model()


@override_settings(IS_TESTING=True)
class HealthWebhookSafetyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="hw-safety", email="hw-safety@x.com", password="p",
        )
        self.service = Service.objects.create(
            name="hw-svc", owner=self.user,
            health_webhook_token="super-secret-webhook-token-1234",
        )
        self.url = f"/api/v1/services/{self.service.id}/health/webhook/"
        self.client = APIClient()

    def _post(self, **overrides):
        body = {"token": "super-secret-webhook-token-1234", "nonce": "n-12345678", "event": "health_update", "status": "healthy"}
        body.update(overrides)
        return self.client.post(self.url, body, format="json")

    def _post_with_headers(self, headers, **overrides):
        body = {"token": "super-secret-webhook-token-1234", "nonce": "n-12345678", "event": "health_update", "status": "healthy"}
        body.update(overrides)
        return self.client.post(self.url, body, format="json", **headers)

    def test_missing_nonce_rejected(self):
        resp = self._post(nonce="")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nonce", str(resp.data).lower())

    def test_short_nonce_rejected(self):
        resp = self._post(nonce="abc")
        self.assertEqual(resp.status_code, 400)

    def test_replayed_nonce_rejected(self):
        first = self._post(nonce="unique-nonce-1")
        self.assertEqual(first.status_code, 200)
        second = self._post(nonce="unique-nonce-1")
        self.assertEqual(second.status_code, 401)
        self.assertIn("nonce", str(second.data).lower())

    def test_nonce_via_header_is_accepted(self):
        first = self._post_with_headers(
            {"HTTP_X_WEBHOOK_NONCE": "header-nonce-1"},
        )
        self.assertEqual(first.status_code, 200)

    def test_unknown_event_rejected(self):
        resp = self._post(event="drop_tables", nonce="ev-12345678")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("event", str(resp.data).lower())

    def test_health_update_healthy_audited(self):
        resp = self._post(nonce="aud-12345678")
        self.assertEqual(resp.status_code, 200)
        log = AuditLog.objects.filter(action="HEALTH_WEBHOOK_APPLIED").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata.get("event"), "health_update")

    def test_unknown_event_audited(self):
        resp = self._post(event="bogus", nonce="bogus-12345678")
        self.assertEqual(resp.status_code, 400)
        log = AuditLog.objects.filter(action="HEALTH_WEBHOOK_REJECTED").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata.get("reason"), "unknown_event")
        self.assertEqual(log.metadata.get("event"), "bogus")

    def test_deploy_complete_event_accepted(self):
        resp = self._post(event="deploy_complete", nonce="dc-12345678")
        self.assertEqual(resp.status_code, 200)
        log = AuditLog.objects.filter(action="DEPLOY_COMPLETE_WEBHOOK").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata.get("event"), "deploy_complete")

    def test_constant_time_compare_still_used(self):
        """Defensive check: the constant-time token compare path is
        the only way the endpoint accepts a request. We don't test
        timing, but we assert that an exact-match token is still
        accepted (the constant-time path is the only path)."""
        # Reset cache so the previous test's nonce doesn't interfere.
        cache.clear()
        resp = self._post(nonce="ct-12345678")
        self.assertEqual(resp.status_code, 200)

    def test_invalid_token_still_rejected(self):
        resp = self.client.post(
            self.url,
            {"token": "wrong-token", "nonce": "n2-12345678", "event": "health_update"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_unconfigured_token_returns_404(self):
        # Mimic the "unconfigured" state the view checks.
        self.service.health_webhook_token = " "
        self.service.save(update_fields=["health_webhook_token"])
        resp = self.client.post(
            self.url,
            {"token": "anything", "nonce": "uc-12345678"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_metadata_records_ip(self):
        self._post(nonce="ip-12345678")
        log = AuditLog.objects.filter(action="HEALTH_WEBHOOK_APPLIED").first()
        self.assertIsNotNone(log)
        self.assertIn("ip", log.metadata)
