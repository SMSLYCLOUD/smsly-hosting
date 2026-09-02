"""Regression tests for the platform-domain tenant-hijack guard.

Attack: a tenant adds grid.smsly.cloud (or any subdomain of the
platform base / any ManagedServer host) as a CUSTOM DOMAIN of their
service. Verification passed trivially because the platform domain
obviously points at the platform; Caddy then routed the platform's own
hostname through the tenant's container — session cookies, admin API,
everything. The guard (_is_platform_owned_domain) must reject the base
domain, its subdomains, and node hosts — but NOT unrelated tenant
domains.
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from apps.deployments.views._helpers import _is_platform_owned_domain


def _cfg(domain="grid.smsly.cloud"):
    cfg = mock.MagicMock()
    cfg.domain = domain
    return cfg


class PlatformOwnedDomainTests(SimpleTestCase):
    def _patch(self, domain="grid.smsly.cloud", servers=None):
        cfg = _cfg(domain)
        server_qs = mock.MagicMock()
        server_qs.exclude.return_value.values_list.return_value.__iter__.return_value = iter(
            servers or []
        )
        server_qs.exclude.return_value.values_list.return_value.__getitem__ = (
            lambda s: list(servers or [])
        )
        # The real code uses .exclude(host="").values_list("host", flat=True)[:200]
        sliced = mock.MagicMock()
        sliced.__iter__ = lambda s: iter(servers or [])
        server_qs.exclude.return_value.values_list.return_value.__getitem__ = lambda s, k: sliced
        server_qs.exclude.return_value.__getitem__ = lambda s, k: sliced
        return mock.patch.multiple(
            "apps.deployments.models.core.PlatformConfig",
            load=mock.Mock(return_value=cfg),
        ), mock.patch(
            "apps.deployments.models.core.ManagedServer",
            server_qs,
        )

    def test_platform_base_domain_is_owned(self):
        with self._patch()[0], self._patch()[1]:
            self.assertTrue(_is_platform_owned_domain("grid.smsly.cloud"))

    def test_platform_parent_zone_apex_is_owned(self):
        # config.domain = grid.smsly.cloud → operator owns the whole
        # smsly.cloud zone. The bare apex was claimable (live-verified
        # 201) before this rule existed.
        with self._patch()[0], self._patch()[1]:
            self.assertTrue(_is_platform_owned_domain("smsly.cloud"))
            self.assertTrue(_is_platform_owned_domain("www.smsly.cloud"))
            self.assertTrue(_is_platform_owned_domain("dev.smsly.cloud"))

    def test_platform_subdomain_is_owned(self):
        with self._patch()[0], self._patch()[1]:
            self.assertTrue(_is_platform_owned_domain("api.grid.smsly.cloud"))
            self.assertTrue(_is_platform_owned_domain("deep.sub.grid.smsly.cloud"))

    def test_case_and_trailing_dot_normalized(self):
        with self._patch()[0], self._patch()[1]:
            self.assertTrue(_is_platform_owned_domain("GRID.SMSLY.CLOUD."))
            self.assertTrue(_is_platform_owned_domain("Api.Grid.Smsly.Cloud."))

    def test_unrelated_tenant_domain_is_not_owned(self):
        with self._patch()[0], self._patch()[1]:
            self.assertFalse(_is_platform_owned_domain("customer-app.example.com"))
            self.assertFalse(_is_platform_owned_domain("grid.smsly.cloud.evil.com"))

    def test_suffix_lookalike_is_not_owned(self):
        # grid.smsly.cloud.evil.com ends with ".com" not ".grid.smsly.cloud"
        with self._patch()[0], self._patch()[1]:
            self.assertFalse(_is_platform_owned_domain("grid.smsly.cloud.evil.com"))

    def test_empty_and_none_safe(self):
        with self._patch()[0], self._patch()[1]:
            self.assertFalse(_is_platform_owned_domain(""))
            self.assertFalse(_is_platform_owned_domain(None))
