"""Wildcard @known_hosts runs Coraza; opt-outs bypass it (regression)."""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, ManagedServer, Service
from apps.deployments.models.addons import Addon
from apps.deployments.models.core import PlatformConfig
from apps.deployments.services.caddy_manager import config_generation as cg
from apps.deployments.services.caddy_manager import validation as cv
from apps.domains.models import Domain, DomainStatus


class WildcardWafTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="wafuser", password="x")
        self.svc = Service.objects.create(
            name="wafsvc", owner=self.user,
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

    def test_coraza_snippet_loads_full_crs(self):
        # Empty ruleset = silent allow-all. The CRS includes must ship.
        self.assertIn("Include @crs-setup.conf.example", cg.CORAZA_SNIPPET)
        self.assertIn("Include @owasp_crs/*.conf", cg.CORAZA_SNIPPET)
        self.assertIn("SecRuleEngine On", cg.CORAZA_SNIPPET)

    def test_coraza_snippet_bypasses_platform_sync(self):
        # Inter-server sync (HMAC-authed downstream) must not be
        # CRS-blocked: 949110 killed every node PATCH with empty 403.
        self.assertIn("X-SMSLY-Remote-Sync", cg.CORAZA_SNIPPET)
        self.assertIn("ctl:ruleEngine=Off", cg.CORAZA_SNIPPET)
        # Must be chained strictly to REQUEST_URI @beginsWith /api/v1/
        self.assertIn('id:1000001,phase:1,pass,nolog,t:none,chain"', cg.CORAZA_SNIPPET)
        self.assertIn(
            'SecRule REQUEST_URI "@beginsWith /api/v1/" "t:none,t:urlDecodeUni,t:normalizePath,ctl:ruleEngine=Off"',
            cg.CORAZA_SNIPPET,
        )
        # Unchained global bypass must NOT exist
        self.assertNotIn(
            'SecRule REQUEST_HEADERS:X-SMSLY-Remote-Sync "@streq 1" "id:1000001,phase:1,pass,nolog,ctl:ruleEngine=Off"',
            cg.CORAZA_SNIPPET,
        )
        # Directives block is backtick-delimited; inner text must have exactly 2 backticks total
        self.assertEqual(cg.CORAZA_SNIPPET.count("`"), 2)

    def test_node_caddyfile_carries_waf(self):
        import inspect
        src = inspect.getsource(cg.generate_node_caddyfile)
        self.assertIn("order coraza_waf first", src)
        self.assertIn("CORAZA_SNIPPET", src)
        self.assertIn("_waf_lines", src)

    def _assert_strips_before_coraza(self, block: str):
        self.assertIn("route {", block)
        strip = "request_header -X-SMSLY-Remote-Sync"
        imp = "import coraza_waf"
        self.assertIn(strip, block)
        self.assertIn(imp, block)
        self.assertLess(block.index("route {"), block.index(strip))
        self.assertLess(block.index(strip), block.index(imp))

    def test_tenant_helper_blocks_strip_remote_sync_before_coraza(self):
        svc_block = cg._build_service_domain_block("app.example.com", "app.example.com")
        self._assert_strips_before_coraza(svc_block)

        svc_optout = cg._build_service_domain_block(
            "app.example.com", "app.example.com", waf_opt_out=True,
        )
        self.assertIn("request_header -X-SMSLY-Remote-Sync", svc_optout)
        self.assertNotIn("import coraza_waf", svc_optout)

        alias_block = cg._build_host_alias_block(
            "alias.example.com", "/login", "http://127.0.0.1:8000", "app.example.com",
        )
        self._assert_strips_before_coraza(alias_block)

        alias_optout = cg._build_host_alias_block(
            "alias.example.com", "/login", "http://127.0.0.1:8000", "app.example.com",
            waf_opt_out=True,
        )
        self.assertIn("request_header -X-SMSLY-Remote-Sync", alias_optout)
        self.assertNotIn("import coraza_waf", alias_optout)

    def test_service_domain_blocks_and_wildcard_strip_remote_sync(self):
        PlatformConfig.clear_cache()
        PlatformConfig.objects.update_or_create(
            id=1,
            defaults={"domain": "trulay.site", "use_ssl": True, "enable_wildcard_subdomains": True},
        )
        primary = ManagedServer.objects.create(
            owner=self.user, name="primary", host="192.0.2.10", is_primary=True, status="ACTIVE",
        )
        provider = CloudProvider.objects.create(
            name="local-waf-test", provider_type=CloudProvider.ProviderType.LOCAL, is_active=True,
        )
        tenant = Service.objects.create(
            name="tenant-app",
            owner=self.user,
            provider=provider,
            server=primary,
            status=Service.Status.ACTIVE,
            public_domain="tenant-app.trulay.site",
            staging_domain="staging.tenant.example.com",
            waf_opt_out=False,
        )
        Domain.objects.create(
            service=tenant,
            domain_name="custom.example.com",
            status=DomainStatus.ACTIVE,
            verified=True,
        )
        Deployment.objects.create(
            service=tenant,
            status=Deployment.Status.STAGED,
            staging_url="https://staging.tenant.example.com",
            commit_hash="abc123",
        )
        Addon.objects.create(
            service=tenant,
            name="addon-db",
            addon_type="POSTGRES",
            public_domain="addon.example.com",
        )

        blocks = cg._get_service_domain_blocks("trulay.site")
        by_head = {b.splitlines()[0]: b for b in blocks}
        self._assert_strips_before_coraza(by_head["custom.example.com {"])
        self._assert_strips_before_coraza(by_head["staging.tenant.example.com {"])
        self._assert_strips_before_coraza(by_head["addon.example.com {"])

        caddyfile = cg.generate_caddyfile(PlatformConfig.load())
        self.assertIn("(coraza_waf)", caddyfile)
        self.assertGreaterEqual(caddyfile.count("import coraza_waf"), 1)
        self.assertEqual(cv.validate_edge_sidecar_endpoints(caddyfile), [])
        self.assertEqual(cv.validate_secure_headers_present(caddyfile), [])
        self.assertEqual(cv.validate_service_routes_do_not_hit_control_plane(caddyfile), [])

        # Wildcard block must strip top-level and inside @known_hosts route before coraza
        wildcard_idx = caddyfile.index("*.trulay.site {")
        wildcard_section = caddyfile[wildcard_idx:]
        self.assertIn("    request_header -X-SMSLY-Remote-Sync", wildcard_section)
        known_idx = wildcard_section.index("handle @known_hosts {")
        known_block = wildcard_section[known_idx:known_idx + 350]
        self._assert_strips_before_coraza(known_block)
        self.assertLess(known_block.index("import coraza_waf"), known_block.index("reverse_proxy"))

    def test_node_caddyfile_separates_control_plane_from_tenant_stripping(self):
        PlatformConfig.clear_cache()
        PlatformConfig.objects.update_or_create(
            id=1, defaults={"domain": "trulay.site"},
        )
        node = ManagedServer.objects.create(
            owner=self.user, name="node-waf", host="198.51.100.2",
            node_number=1, wg_address="10.100.0.2",
        )
        provider = CloudProvider.objects.create(
            name="remote-waf-test", provider_type=CloudProvider.ProviderType.REMOTE, is_active=True,
        )
        Service.objects.create(
            name="node-tenant",
            owner=self.user,
            provider=provider,
            server=node,
            node_url_enabled=True,
            public_domain="node-tenant-e1.trulay.site",
            internal_port=8000,
        )
        content = cg.generate_node_caddyfile(node)
        self.assertEqual(cv.validate_edge_sidecar_endpoints(content), [])
        self.assertEqual(cv.validate_secure_headers_present(content), [])

        blocks = cv._split_site_blocks(content)
        mgmt = blocks["grid1.trulay.site"]
        self.assertIn("import coraza_waf", mgmt)
        self.assertNotIn("request_header -X-SMSLY-Remote-Sync", mgmt)

        self.assertIn("request_header -X-SMSLY-Remote-Sync", blocks["node-tenant-grid1.trulay.site:80"])
        self._assert_strips_before_coraza(blocks["node-tenant.grid1.trulay.site"])
        self._assert_strips_before_coraza(blocks["node-tenant-node1.trulay.site"])
        self._assert_strips_before_coraza(blocks["node-tenant-e1.trulay.site"])
