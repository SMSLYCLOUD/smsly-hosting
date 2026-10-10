"""Wildcard @known_hosts runs Coraza; opt-outs bypass it (regression)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.services.caddy_manager import config_generation as cg


class WildcardWafTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="wafuser", password="x")
        self.svc = Service.objects.create(
            name="wafsvc", owner=user,
            public_domain="wafsvc.trulay.site", waf_opt_out=True,
        )

    def test_optout_helper_lists_opted_out_host(self):
        self.assertEqual(
            cg._get_wildcard_waf_optout_hosts("trulay.site"),
            ["wafsvc.trulay.site"],
        )

    def test_optout_helper_empty_without_domain(self):
        self.assertEqual(cg._get_wildcard_waf_optout_hosts(""), [])

    def test_optout_helper_ignores_other_suffix(self):
        self.assertEqual(
            cg._get_wildcard_waf_optout_hosts("example.com"), [])

    def test_known_hosts_handle_imports_coraza(self):
        import inspect
        src = inspect.getsource(cg.generate_caddyfile)
        self.assertIn("import coraza_waf", src)
        self.assertIn("@waf_optout", src)
        # Opt-out handle must precede known_hosts (first match wins).
        self.assertLess(src.index("@waf_optout"), src.index("@known_hosts host"))
