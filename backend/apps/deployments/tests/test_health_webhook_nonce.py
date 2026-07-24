# pylint: disable=invalid-name
"""Tests for Issue 14 (ServiceHealthWebhookView hardening, S batch)."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.deployments.models.audit import AuditLog
from apps.deployments.models.core import Service

User = get_user_model()


@override_settings(IS_TESTING=True)
class HealthWebhookNonceHeaderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="hw-nonce", email="hw-nonce@x.com", password="p",
        )
        self.service = Service.objects.create(
            name="hw-nonce-svc", owner=self.user,
            health_webhook_token="super-secret-webhook-token-1234",
        )
        self.url = f"/api/v1/services/{self.service.id}/health/webhook/"
        self.client = APIClient()

    def _post(self, headers=None, **overrides):
        body = {
            "token": "super-secret-webhook-token-1234",
            "event": "health_update",
            "status": "healthy",
        }
        body.update(overrides)
        return self.client.post(self.url, body, format="json", **headers or {})

    def test_smsly_webhook_nonce_header_is_required(self):
        resp = self._post(headers={"HTTP_X_SMSLY_WEBHOOK_NONCE": "fresh-nonce-001234"})
        self.assertEqual(resp.status_code, 200)

    def test_missing_nonce_header_returns_400(self):
        resp = self.client.post(
            self.url,
            {"token": "super-secret-webhook-token-1234", "event": "health_update"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("nonce", str(resp.data).lower())

    def test_replay_with_same_nonce_is_rejected(self):
        first = self._post(headers={"HTTP_X_SMSLY_WEBHOOK_NONCE": "replay-nonce-001234"})
        self.assertEqual(first.status_code, 200)
        second = self._post(headers={"HTTP_X_SMSLY_WEBHOOK_NONCE": "replay-nonce-001234"})
        self.assertEqual(second.status_code, 401)
        self.assertIn("nonce", str(second.data).lower())


@override_settings(IS_TESTING=True)
class HealthWebhookLastUsedAtTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="hw-last-used", email="hw-lu@x.com", password="p",
        )
        self.service = Service.objects.create(
            name="hw-last-used-svc", owner=self.user,
            health_webhook_token="super-secret-webhook-token-1234",
        )
        self.url = f"/api/v1/services/{self.service.id}/health/webhook/"
        self.client = APIClient()

    def _post(self, **overrides):
        body = {
            "token": "super-secret-webhook-token-1234",
            "event": "health_update",
            "status": "healthy",
        }
        body.update(overrides)
        return self.client.post(self.url, body, format="json")

    def test_last_used_at_starts_null(self):
        self.assertIsNone(self.service.health_webhook_token_last_used_at)

    def test_last_used_at_updated_on_successful_health_update(self):
        before = timezone.now()
        resp = self._post(nonce="track-last-used-001")
        self.assertEqual(resp.status_code, 200)
        self.service.refresh_from_db()
        self.assertIsNotNone(self.service.health_webhook_token_last_used_at)
        self.assertGreaterEqual(self.service.health_webhook_token_last_used_at, before)

    def test_last_used_at_updated_on_unhealthy(self):
        resp = self._post(nonce="track-unhealthy-001", status="unhealthy")
        self.assertEqual(resp.status_code, 200)
        self.service.refresh_from_db()
        self.assertIsNotNone(self.service.health_webhook_token_last_used_at)


@override_settings(IS_TESTING=True)
class HealthWebhookTokenExpiryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="hw-expiry", email="hw-expiry@x.com", password="p",
        )
        self.service = Service.objects.create(
            name="hw-expiry-svc", owner=self.user,
            health_webhook_token="super-secret-webhook-token-1234",
        )
        self.url = f"/api/v1/services/{self.service.id}/health/webhook/"
        self.client = APIClient()

    def _post(self, **overrides):
        body = {
            "token": "super-secret-webhook-token-1234",
            "event": "health_update",
            "status": "healthy",
        }
        body.update(overrides)
        return self.client.post(self.url, body, format="json")

    def test_idle_token_beyond_default_90_days_rejected(self):
        stale = timezone.now() - timedelta(days=91)
        self.service.health_webhook_token_last_used_at = stale
        self.service.save(update_fields=["health_webhook_token_last_used_at"])
        resp = self._post(nonce="idle-token-001")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("90 days", str(resp.data))

    def test_idle_token_within_90_days_accepted(self):
        recent = timezone.now() - timedelta(days=89)
        self.service.health_webhook_token_last_used_at = recent
        self.service.save(update_fields=["health_webhook_token_last_used_at"])
        resp = self._post(nonce="idle-token-002")
        self.assertEqual(resp.status_code, 200)

    def test_token_never_used_is_accepted(self):
        resp = self._post(nonce="never-used-001")
        self.assertEqual(resp.status_code, 200)

    def test_idle_rejection_creates_audit_log(self):
        stale = timezone.now() - timedelta(days=120)
        self.service.health_webhook_token_last_used_at = stale
        self.service.save(update_fields=["health_webhook_token_last_used_at"])
        resp = self._post(nonce="idle-token-003")
        self.assertEqual(resp.status_code, 401)
        log = AuditLog.objects.filter(
            action="HEALTH_WEBHOOK_REJECTED",
            target=f"Service:{self.service.id}",
        ).filter(metadata__reason="token_idle_expired").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata.get("max_idle_days"), 90)


@override_settings(IS_TESTING=True, IS_CELERY_TEST=True)
class HealthWebhookCacheTtlTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="hw-ttl", email="hw-ttl@x.com", password="p",
        )
        self.service = Service.objects.create(
            name="hw-ttl-svc", owner=self.user,
            health_webhook_token="super-secret-webhook-token-1234",
        )
        self.url = f"/api/v1/services/{self.service.id}/health/webhook/"
        self.client = APIClient()

    def test_5_minute_cache_ttl(self):
        from apps.deployments.views.health_webhook import _NONCE_CACHE_TTL_SECONDS
        self.assertEqual(_NONCE_CACHE_TTL_SECONDS, 300)
