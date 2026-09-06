"""Tests for the startup Caddy sync outage guard.

On 2026-09-06 a backend restart ran the startup Caddy sync against a
ghost/empty-domain PlatformConfig. The generator emitted a stub
Caddyfile without the control-plane site block, apply_caddyfile's own
guard ALSO skipped (same empty domain), the stub went live, Caddy
dropped its 443 listener, and every proxied request 521'd until manual
regeneration.
"""
from types import SimpleNamespace

from django.test import TestCase

from apps.deployments.services.startup import startup_sync_skip_reason


class StartupSyncGuardTests(TestCase):
    def test_healthy_config_proceeds(self):
        cfg = SimpleNamespace(_is_ghost=False, domain="grid.smsly.cloud")
        self.assertIsNone(startup_sync_skip_reason(cfg))

    def test_ghost_config_skips(self):
        cfg = SimpleNamespace(_is_ghost=True, domain="grid.smsly.cloud")
        reason = startup_sync_skip_reason(cfg)
        self.assertIsNotNone(reason)
        self.assertIn("ghost", reason)

    def test_empty_domain_skips(self):
        for domain in ("", "   ", None):
            cfg = SimpleNamespace(_is_ghost=False, domain=domain)
            reason = startup_sync_skip_reason(cfg)
            self.assertIsNotNone(reason, f"domain={domain!r} must skip")

    def test_none_config_skips(self):
        self.assertIsNotNone(startup_sync_skip_reason(None))

    def test_missing_attrs_skips_safe(self):
        # A config object without the expected attributes must not
        # crash the decision — fail closed (skip).
        cfg = SimpleNamespace()
        reason = startup_sync_skip_reason(cfg)
        self.assertIsNotNone(reason)
