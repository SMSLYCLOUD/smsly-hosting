# pylint: disable=invalid-name
"""Structural tests for the GENERATED Caddyfile.

The old suite asserted on a checked-in ``caddy-config/Caddyfile`` fixture
written for a long-dead static template (``{$DOMAIN}`` placeholders,
``@api path`` matchers). The platform renders its Caddyfile from
``generate_caddyfile`` on every routing sync, so these tests render with
a seeded PlatformConfig and assert on that output instead — including
the redirect invariants (http->https 308, /ui canonical, wildcard
redirect handles authoritative).
"""

from django.test import TestCase

from apps.deployments.models import PlatformConfig
from apps.deployments.services.caddy_manager.config_generation import (
    generate_caddyfile,
)
from apps.deployments.services.caddy_manager.validation import (
    extract_site_labels,
    validate_wildcard_redirects_authoritative,
)


class CaddyfileValidityTests(TestCase):
    """Verify the generated Caddyfile has correct structure and redirects."""

    @classmethod
    def setUpTestData(cls):
        config = PlatformConfig.load()
        config.domain = "grid.example.test"
        config.use_ssl = True
        config.wildcard_subdomains = True
        config.server_ip = None
        config.save()
        cls.caddyfile = generate_caddyfile(config)
        cls.domain = "grid.example.test"

    def test_global_options_block_is_first(self):
        """Exactly one keyless global block, preceding every site block."""
        meaningful = [
            line for line in self.caddyfile.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertTrue(meaningful[0] == "{")
        self.assertIn("on_demand_tls", self.caddyfile)

    def test_no_nginx_references(self):
        for line in self.caddyfile.split("\n"):
            stripped = line.strip()
            if "reverse_proxy" in stripped and "nginx" in stripped:
                self.fail(f"Caddyfile still proxies to nginx: {stripped}")

    def test_proxies_to_backend(self):
        self.assertIn("reverse_proxy backend:8000", self.caddyfile)

    def test_proxies_to_frontend(self):
        self.assertIn("reverse_proxy frontend:3000", self.caddyfile)

    def test_on_demand_tls_asks_backend(self):
        self.assertIn(
            "ask http://backend:8000/api/v1/services/check-domain/", self.caddyfile
        )

    def test_http_to_https_redirect(self):
        """The :80 block redirects non-IP hosts to HTTPS (308)."""
        self.assertIn("redir @redirectable https://{host}{uri} 308", self.caddyfile)

    def test_platform_block_present(self):
        self.assertIn(f"{self.domain} {{", self.caddyfile)

    def test_wildcard_site_present(self):
        self.assertIn(f"*.{self.domain} {{", self.caddyfile)
        self.assertIn(self.domain, extract_site_labels(self.caddyfile))

    def test_ui_redirect_canonical(self):
        """/ui collapses to / everywhere a platform :443 variant exists."""
        self.assertIn("handle /ui {", self.caddyfile)
        self.assertIn("redir / 301", self.caddyfile)

    def test_wildcard_redirects_authoritative(self):
        """No redirect source may also be an explicit site (dead 301)."""
        self.assertEqual(validate_wildcard_redirects_authoritative(self.caddyfile), [])

    def test_gzip_encoding(self):
        self.assertIn("encode gzip", self.caddyfile)
