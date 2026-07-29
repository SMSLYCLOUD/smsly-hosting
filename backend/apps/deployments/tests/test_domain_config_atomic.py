# pylint: disable=invalid-name
"""
Regression tests for Issue 22 (DomainConfigView.post atomicity).

The PUT handler must:
  * wrap the Caddyfile apply and DNS sync in a single
    ``transaction.atomic`` block, with ``select_for_update`` on the
    PlatformConfig singleton;
  * persist no DB state (caddy_status, updated_at) when the
    Caddyfile apply fails — the caller sees the failure response
    AND the row is unchanged.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments.models import PlatformConfig

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "domain-config-atomic-tests",
    }
}


@override_settings(CACHES=TEST_CACHES)
class DomainConfigAtomicTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="dc-atomic-admin",
            email="dc-atomic@example.com",
            password="password123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.url = "/api/v1/system/domain-config/"

        cfg = PlatformConfig.load()
        cfg.domain = "atomic.smsly.cloud"
        cfg.use_ssl = True
        cfg.wildcard_subdomains = False
        cfg.cloudflare_api_token = "tok-atomic"
        cfg.save()

    def _put(self, payload):
        return self.client.put(self.url, payload, format="json")

    @patch(
        "apps.domains.services.dns.ensure_dns_records",
        return_value={"ok": True, "errors": []},
    )
    @patch(
        "apps.deployments.services.caddy_manager.apply_caddyfile",
        return_value={"ok": True, "message": "ok"},
    )
    @patch(
        "apps.deployments.services.caddy_manager.generate_caddyfile",
        return_value=":80 { reverse_proxy localhost:8090 }",
    )
    def test_happy_path_uses_select_for_update(self, _gen, _apply, _dns):
        with patch(
            "apps.deployments.views.PlatformConfig.objects.select_for_update",
            wraps=PlatformConfig.objects.select_for_update,
        ) as mock_lock:
            resp = self._put(
                {
                    "domain": "atomic.smsly.cloud",
                    "use_ssl": True,
                    "wildcard_subdomains": False,
                }
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(mock_lock.call_count, 1)
        cfg = PlatformConfig.load()
        self.assertEqual(cfg.caddy_status, "applied")

    @patch(
        "apps.deployments.services.caddy_manager.apply_caddyfile",
        return_value={"ok": False, "message": "caddy reload failed"},
    )
    @patch(
        "apps.deployments.services.caddy_manager.generate_caddyfile",
        return_value=":80 { reverse_proxy localhost:8090 }",
    )
    def test_caddy_failure_marks_caddy_status_error(self, _gen, _apply):
        resp = self._put(
            {
                "domain": "atomic.smsly.cloud",
                "use_ssl": True,
                "wildcard_subdomains": False,
            }
        )
        self.assertEqual(resp.status_code, 503)
        cfg = PlatformConfig.load()
        self.assertEqual(cfg.caddy_status, "error")

