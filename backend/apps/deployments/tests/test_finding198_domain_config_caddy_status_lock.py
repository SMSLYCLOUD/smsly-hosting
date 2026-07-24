# pylint: disable=invalid-name
"""Regression tests for Finding #198 (``DomainConfigView`` caddy_status
lock).

Two concurrent admins hitting ``PUT /api/v1/system/domain-config/``
must not be able to interleave their reads and writes on the
``PlatformConfig`` singleton. The Caddyfile is applied, then
``caddy_status`` is written, then the DNS sync is attempted. If the
DNS sync fails, the operator must see no DB state change at all
(``caddy_status`` still shows the old value, ``updated_at`` is
unchanged).

The fix wraps the entire ``put()`` body in ``transaction.atomic``,
opens it with ``select_for_update().get(pk=1)``, and rolls back
automatically when the DNS sync raises.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments.models import PlatformConfig

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fix198-tests",
    }
}


@override_settings(CACHES=TEST_CACHES)
class Finding198DomainConfigLockTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="fix198-admin",
            email="fix198@example.com",
            password="p",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = "/api/v1/system/domain-config/"

        cfg = PlatformConfig.load()
        cfg.domain = "fix198.smsly.cloud"
        cfg.use_ssl = True
        cfg.wildcard_subdomains = False
        cfg.cloudflare_api_token = "tok-fix198"
        cfg.save()

    def test_uses_select_for_update(self):
        from django.db.models import QuerySet

        original = QuerySet.select_for_update
        lock_mock = MagicMock()

        def _fake(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original(self, *args, **kwargs)

        with patch(
            "django.db.models.QuerySet.select_for_update",
            new=_fake,
        ), patch(
            "apps.deployments.services.caddy_manager.apply_caddyfile",
            return_value={"ok": True, "message": "ok"},
        ), patch(
            "apps.deployments.services.dns.ensure_dns_records",
            return_value={"ok": True, "errors": []},
        ):
            resp = self.client.put(
                self.url,
                {"domain": "fix198b.smsly.cloud"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(lock_mock.call_count, 1)

    def test_caddy_apply_failure_keeps_lock(self):
        """If ``apply_caddyfile`` raises, the response is 5xx and
        the config save runs inside the same ``transaction.atomic``
        block — the row is still lockable by a concurrent admin."""
        from django.db.models import QuerySet

        original = QuerySet.select_for_update
        lock_mock = MagicMock()

        def _fake(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original(self, *args, **kwargs)

        with patch(
            "apps.deployments.services.caddy_manager.apply_caddyfile",
            side_effect=RuntimeError("caddy boom"),
        ), patch(
            "django.db.models.QuerySet.select_for_update",
            new=_fake,
        ):
            resp = self.client.put(
                self.url,
                {"domain": "fix198-fail.smsly.cloud"},
                format="json",
            )
        self.assertEqual(resp.status_code, 500)
        self.assertGreaterEqual(lock_mock.call_count, 1)
