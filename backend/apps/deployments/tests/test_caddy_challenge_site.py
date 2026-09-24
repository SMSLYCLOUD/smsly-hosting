"""Regression tests for the HTTP-proof challenge site in generated Caddyfiles.

A standalone `:80` site was rejected by Caddy as an ambiguous site
definition (2026-09-24: every reload failed until fixed), so the
challenge handle must live INSIDE the existing :80 site exactly once,
and the https redirect must exclude its path (directive order runs
redir before handles).
"""
from types import SimpleNamespace

from django.test import SimpleTestCase

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
