"""Regression tests for the HTTP-proof challenge site in generated Caddyfiles.

A standalone `:80` site was rejected by Caddy as an ambiguous site
definition (2026-09-24: every reload failed until fixed), so the
challenge handle must live INSIDE the existing :80 site exactly once,
and the https redirect must exclude its path (directive order runs
redir before handles).
"""
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from apps.deployments.services.caddy_manager.config_generation import (
    generate_caddyfile,
)


class ChallengeSiteTests(SimpleTestCase):
    def _config(self, domain="cloud.smsly.cloud", use_ssl=True):
        return SimpleNamespace(
            domain=domain,
            use_ssl=use_ssl,
            wildcard_subdomains=False,
            cloudflare_api_token="",
        )

    def test_single_port80_site_with_challenge_handle(self):
        caddyfile = generate_caddyfile(self._config())
        self.assertEqual(caddyfile.count(":80 {"), 1)
        self.assertIn("handle /.well-known/smsly-verify/* {", caddyfile)
        self.assertIn("header_up Host localhost", caddyfile)

    def test_https_redirect_excludes_challenge_path(self):
        caddyfile = generate_caddyfile(self._config())
        self.assertIn("not path /.well-known/smsly-verify/*", caddyfile)

    def test_no_domain_build_also_serves_challenges(self):
        caddyfile = generate_caddyfile(self._config(domain="", use_ssl=False))
        self.assertEqual(caddyfile.count(":80 {"), 1)
        self.assertIn("handle /.well-known/smsly-verify/* {", caddyfile)


class PerDomainChallengeTests(TestCase):
    """Per-hostname site blocks (not just the generic :80 site) must
    answer the challenge: Caddy routes Host-based sites ahead of the
    generic one, and the per-domain :80 auto-redirect otherwise 308s
    the token to https where the app 404s — permanently breaking
    orange-clouded verification (trulay.co, 2026-09-24)."""

    def test_per_domain_site_serves_challenge_before_proxy(self):
        from django.contrib.auth.models import User
        from apps.cloud.models import CloudProvider
        from apps.deployments.models import Service
        from apps.domains.models import Domain, DomainStatus
        user = User.objects.create_user(
            username="chal-owner", email="c@example.com", password="x")
        provider = CloudProvider.objects.create(
            name="chal-prov",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True)
        svc = Service.objects.create(
            name="chal-svc", owner=user, provider=provider,
            public_domain="chal-svc.cloud.smsly.cloud")
        Domain.objects.create(
            domain_name="example-chal.com", service=svc,
            status=DomainStatus.DNS_VERIFIED, verified=True)
        caddyfile = generate_caddyfile(SimpleNamespace(
            domain="cloud.smsly.cloud", use_ssl=True,
            wildcard_subdomains=False, cloudflare_api_token=""))
        block = caddyfile[caddyfile.index("example-chal.com {"):]
        handle_at = block.index("handle /.well-known/smsly-verify/* {")
        self.assertLess(handle_at, block.index("reverse_proxy",
                                               handle_at + 1) if "reverse_proxy" in block[handle_at:] else 10**9)
        self.assertIn("header_up Host localhost", block)
