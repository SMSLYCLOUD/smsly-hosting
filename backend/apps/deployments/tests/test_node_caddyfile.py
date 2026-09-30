"""Tests for per-node Caddyfile generation (node-hosted services).

Covers the flat second-level scheme ({slug}-grid{N}.{zone}, CF-safe)
plus the nested direct-access form, and container-name upstreams.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import ManagedServer, Service
from apps.deployments.models.core import PlatformConfig
from apps.deployments.services.caddy_manager.config_generation import (
    generate_node_caddyfile,
    node_service_domain,
    node_service_domain_nested,
)


class NodeServiceDomainTests(TestCase):
    def test_flat_scheme_second_level(self):
        self.assertEqual(
            node_service_domain("my-app", 1, "trulay.site"),
            "my-app-grid1.trulay.site",
        )

    def test_flat_scheme_strips_first_label(self):
        self.assertEqual(
            node_service_domain("my-app", 2, "grid.smsly.cloud"),
            "my-app-grid2.smsly.cloud",
        )

    def test_nested_scheme_unchanged(self):
        self.assertEqual(
            node_service_domain_nested("my-app", 1, "trulay.site"),
            "my-app.grid1.trulay.site",
        )


class GenerateNodeCaddyfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="node-caddy-owner",
            email="node-caddy-owner@example.com",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="node-caddy-provider",
            provider_type=CloudProvider.ProviderType.REMOTE,
            is_active=True,
        )
        PlatformConfig.objects.update_or_create(
            id=1, defaults={"domain": "trulay.site"},
        )
        self.node = ManagedServer.objects.create(
            owner=self.user,
            name="aws-full-1",
            host="13.60.6.171",
            node_number=1,
            wg_address="10.100.0.2",
        )

    def _svc(self, name="my-app"):
        return Service.objects.create(
            name=name,
            owner=self.user,
            provider=self.provider,
            server=self.node,
            node_url_enabled=True,
            internal_port=8000,
        )

    def test_service_blocks_flat_and_nested(self):
        self._svc()
        content = generate_node_caddyfile(self.node)
        # Flat hostname over plain HTTP (master→node proxy path).
        self.assertIn("http://my-app-grid1.trulay.site {", content)
        # Nested hostname over HTTPS (direct grey access).
        self.assertIn("my-app.grid1.trulay.site {", content)

    def test_upstream_uses_container_name_not_localhost(self):
        self._svc()
        content = generate_node_caddyfile(self.node)
        self.assertIn("reverse_proxy my-app:8000 {", content)
        self.assertNotIn("localhost:8000", content)

    def test_other_node_services_excluded(self):
        other = ManagedServer.objects.create(
            owner=self.user,
            name="other-node",
            host="13.60.6.172",
            node_number=2,
            wg_address="10.100.0.3",
        )
        Service.objects.create(
            name="other-app",
            owner=self.user,
            provider=self.provider,
            server=other,
            node_url_enabled=True,
        )
        self._svc()
        content = generate_node_caddyfile(self.node)
        self.assertIn("my-app-grid1.trulay.site", content)
        self.assertNotIn("other-app", content)
