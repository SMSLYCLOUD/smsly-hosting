"""Regression tests for inline OpenAppSec WAF routing and Grafana wake fallback."""
import inspect
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase

from apps.deployments.models import Service
from apps.deployments.models.core import PlatformConfig
from apps.deployments.services.caddy_manager import config_generation as cg
from apps.deployments.services.caddy_manager.upstream import _service_proxy_upstream


class ServiceProxyUpstreamTests(SimpleTestCase):
    def test_inline_waf_enabled_prepends_appsec_before_traefik(self):
        self.assertEqual(
            _service_proxy_upstream(openappsec_inline=True, waf_opt_out=False),
            "appsec-agent:8082 traefik:80",
        )

    def test_waf_opt_out_bypasses_inline_waf(self):
        self.assertEqual(
            _service_proxy_upstream(openappsec_inline=True, waf_opt_out=True),
            "traefik:80",
        )

    def test_inline_waf_disabled_routes_direct_to_traefik(self):
        self.assertEqual(
            _service_proxy_upstream(openappsec_inline=False, waf_opt_out=False),
            "traefik:80",
        )


class CaddyOpenAppSecInlineTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="inlinewafuser", password="x"
        )
        self.svc_protected = Service.objects.create(
            name="protectedapp",
            owner=self.user,
            public_domain="protectedapp.example.com",
            waf_opt_out=False,
        )
        self.svc_optout = Service.objects.create(
            name="optoutapp",
            owner=self.user,
            public_domain="optoutapp.example.com",
            waf_opt_out=True,
        )

    def test_openappsec_enabled_wires_inline_waf_with_first_failover(self):
        PlatformConfig.clear_cache()
        PlatformConfig.objects.update_or_create(
            id=1,
            defaults={
                "domain": "example.com",
                "use_ssl": True,
                "enable_wildcard_subdomains": True,
                "openappsec_enabled": True,
            },
        )
        caddyfile = cg.generate_caddyfile(PlatformConfig.load())

        # Wildcard @known_hosts routes through inline WAF -> traefik failover
        self.assertIn("reverse_proxy appsec-agent:8082 traefik:80", caddyfile)
        self.assertIn("lb_policy first", caddyfile)
        self.assertIn("dial_timeout 1s", caddyfile)
        self.assertIn("max_fails 1", caddyfile)

        # Opt-out block routes straight to traefik:80 (no appsec-agent:8082)
        optout_idx = caddyfile.index("handle @waf_optout {")
        known_idx = caddyfile.index("handle @known_hosts {")
        optout_section = caddyfile[optout_idx:known_idx]
        self.assertIn("reverse_proxy traefik:80", optout_section)
        self.assertNotIn("appsec-agent:8082", optout_section)

        # Grafana wake fallback: proxy to grafana:3000 with backend:8000 wake fallback
        self.assertIn("handle /grafana/* {", caddyfile)
        self.assertIn("reverse_proxy grafana:3000 backend:8000", caddyfile)

    def test_openappsec_disabled_keeps_direct_traefik_upstream(self):
        PlatformConfig.clear_cache()
        PlatformConfig.objects.update_or_create(
            id=1,
            defaults={
                "domain": "example.com",
                "use_ssl": True,
                "enable_wildcard_subdomains": True,
                "openappsec_enabled": False,
            },
        )
        caddyfile = cg.generate_caddyfile(PlatformConfig.load())
        self.assertNotIn("appsec-agent:8082", caddyfile)
        self.assertIn("reverse_proxy traefik:80", caddyfile)


class GrafanaWakeFallbackViewTests(SimpleTestCase):
    def test_grafana_wake_route_wired(self):
        from apps.core.views import observability as obs_views

        src = inspect.getsource(obs_views.grafana_wake_fallback_view)
        self.assertIn('ensure_tier_awake("ui"', src)
        self.assertIn("Starting Grafana dashboard", src)
