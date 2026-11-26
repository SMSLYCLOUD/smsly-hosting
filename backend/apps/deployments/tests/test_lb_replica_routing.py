"""Regression tests for multi-node LB, blue-green replica pooling, overflow WRR, and node Caddyfile."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.autoscaler.models.replica import ServiceReplica
from apps.cloud.models import CloudProvider
from apps.deployments.models import ManagedServer, Project, Service
from apps.deployments.models.core import PlatformConfig
from apps.deployments.services.caddy_manager.config_generation import (
    generate_node_caddyfile,
    node_service_domains_for_service,
)
from apps.deployments.services.caddy_manager.upstream import (
    _remote_upstream_url_for_service,
)
from apps.deployments.services.replica_parity import (
    replica_sablier_labels,
    resolve_traefik_service_name,
    traefik_service_block,
)
from apps.deployments.services.service_ha import ServiceHAManager
from apps.deployments.services.spawning_service import SpawningService
from apps.deployments.services.traefik_manager.canary_file import (
    _iter_router_services,
)
from apps.deployments.services.traefik_manager.overflow_file import (
    build_overflow_config,
)

User = get_user_model()


class ReplicaParityAndCanaryFixTests(SimpleTestCase):
    def test_discovers_blue_green_slot_and_sanitized_service_blocks(self):
        bg_labels = {
            "traefik.enable": "true",
            "traefik.http.routers.my-app-bg-a1b2c3.rule": "Host(`app.example.com`)",
            "traefik.http.routers.my-app-bg-a1b2c3.service": "my-app-bg-a1b2c3",
            "traefik.http.services.my-app-bg-a1b2c3.loadbalancer.server.port": "8000",
            "traefik.http.services.my-app-bg-a1b2c3.loadbalancer.healthcheck.path": "/healthz",
        }
        self.assertEqual(resolve_traefik_service_name(bg_labels, "my_app"), "my-app-bg-a1b2c3")
        block = traefik_service_block(bg_labels, "my_app")
        self.assertEqual(
            block,
            {
                "traefik.http.services.my-app-bg-a1b2c3.loadbalancer.server.port": "8000",
                "traefik.http.services.my-app-bg-a1b2c3.loadbalancer.healthcheck.path": "/healthz",
            },
        )

    def test_iter_router_services_handles_explicit_service_ref(self):
        labels = {
            "traefik.http.routers.shop.rule": "Host(`shop.example.com`)",
            "traefik.http.routers.shop.service": "shop-service",
            "traefik.http.services.shop-service.loadbalancer.server.port": "8000",
        }
        self.assertEqual(list(_iter_router_services(labels)), [("shop", "shop-service")])

    def test_sablier_labels_carried_to_replicas_when_enrolled(self):
        svc = SimpleNamespace(name="web", sablier_enabled=True)
        with patch("apps.deployments.services.internal_wake.service_has_mesh_sidecar", return_value=False):
            labels = replica_sablier_labels(svc, {})
        self.assertEqual(labels["sablier.enable"], "true")
        self.assertEqual(labels["sablier.group"], "web")
        self.assertEqual(labels["traefik.docker.allownonrunning"], "true")

    def test_spawn_local_pools_with_promoted_bg_primary_and_sets_service_id(self):
        primary = MagicMock()
        primary.name = "shop"
        primary.status = "running"
        primary.image.tags = ["registry:5000/shop:latest"]
        primary.attrs = {
            "Config": {
                "Env": ["PORT=8000"],
                "Labels": {
                    "traefik.enable": "true",
                    "traefik.http.routers.shop-bg-abc123.rule": "Host(`shop.example.com`)",
                    "traefik.http.routers.shop-bg-abc123.service": "shop-bg-abc123",
                    "traefik.http.services.shop-bg-abc123.loadbalancer.server.port": "8000",
                    "sablier.enable": "true",
                    "sablier.group": "shop",
                },
            }
        }
        client = MagicMock()
        client.containers.get.return_value = primary
        client.containers.list.return_value = []
        new_c = MagicMock()
        new_c.id = "r" * 64
        client.containers.run.return_value = new_c

        svc = MagicMock()
        svc.name = "shop"
        svc.id = "11111111-2222-3333-4444-555555555555"
        svc.project = None
        svc.docker_image = "registry:5000/shop:latest"
        svc.internal_port = 8000
        svc.public_domain = "shop.example.com"
        svc.memory_mb = 512
        svc.cpu_cores = 1.0
        svc.registry_credential_id = None
        svc.sablier_enabled = True
        svc.env_vars.all.return_value = []

        replica = MagicMock()
        replica.node = None
        replica.id.hex = "deadbeef"

        with patch("docker.from_env", return_value=client), \
                patch("apps.deployments.models.PlatformConfig"), \
                patch("apps.deployments.services.spawning_service.ensure_scoped_network"), \
                patch("apps.deployments.services.spawning_service._attach_service_addons_to_scoped_net"), \
                patch("apps.deployments.services.spawning_service.get_runtime_for_container", return_value="runc"), \
                patch("apps.deployments.services.spawning_service.get_mtls_labels", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_env_vars", return_value={}), \
                patch("apps.deployments.services.spawning_service.get_mtls_docker_run_volumes", return_value={}), \
                patch("apps.deployments.services.spawning_service._sync_routing_after_replica_change"):
            SpawningService().spawn_local(svc, replica)

        labels = client.containers.run.call_args[1]["labels"]
        self.assertEqual(
            labels["traefik.http.services.shop-bg-abc123.loadbalancer.server.port"],
            "8000",
        )
        self.assertEqual(labels["smsly.service_id"], svc.id)
        self.assertEqual(labels["sablier.group"], "shop")

    def test_overflow_wrr_config_balances_local_and_remote_nodes(self):
        cfg = build_overflow_config(
            {
                "live_router": "shop-bg-1",
                "live_rule": "Host(`shop.example.com`)",
                "live_priority": 100,
                "entrypoints": ["web"],
                "middlewares": ["crowdsec-bouncer@docker"],
                "tls_resolver": "",
                "live_svc": "shop-bg-1",
            },
            local_weight=2,
            remote_targets=[{"url": "http://10.100.0.2:80", "mesh_http": True, "weight": 3}],
        )
        router = cfg["http"]["routers"]["shop-bg-1-overflow"]
        self.assertEqual(router["priority"], 120)
        wrr = cfg["http"]["services"]["shop-bg-1-overflow-wrr"]["weighted"]["services"]
        self.assertEqual(wrr[0], {"name": "shop-bg-1@docker", "weight": 2})
        self.assertEqual(wrr[1]["weight"], 3)
        self.assertIn("shop-bg-1-overflow-proto", cfg["http"]["middlewares"])


class MultiNodeRoutingFixTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lb-user", password="x")
        self.provider = CloudProvider.objects.create(
            name="lb-remote", provider_type=CloudProvider.ProviderType.REMOTE, is_active=True,
        )
        PlatformConfig.objects.update_or_create(id=1, defaults={"domain": "trulay.site"})
        self.node1 = ManagedServer.objects.create(
            owner=self.user, name="n1", host="13.60.6.171", node_number=1, wg_address="10.100.0.2",
        )
        self.node2 = ManagedServer.objects.create(
            owner=self.user, name="n2", host="13.60.6.172", node_number=2, wg_address="10.100.0.3",
        )
        self.project = Project.objects.create(name="lb-proj", owner=self.user)
        self.service = Service.objects.create(
            name="multi-app",
            owner=self.user,
            project=self.project,
            provider=self.provider,
            server=self.node1,
            public_domain="multi-app.trulay.site",
            node_url_enabled=True,
            internal_port=8000,
        )

    def test_remote_upstream_and_node_caddyfile_route_across_replica_nodes(self):
        ServiceReplica.objects.create(
            service=self.service, node=self.node2, container_name="multi-app-replica-n2", status="RUNNING",
        )
        upstreams = _remote_upstream_url_for_service(self.service)
        self.assertIn("https://10.100.0.2:443", upstreams)
        self.assertIn("https://10.100.0.3:443", upstreams)

        node2_caddy = generate_node_caddyfile(self.node2)
        self.assertIn("multi-app.trulay.site:80 {", node2_caddy)
        self.assertIn("reverse_proxy traefik:80 {", node2_caddy)

        # Node1 also switches to traefik:80 once it has a RUNNING replica on node1.
        ServiceReplica.objects.create(
            service=self.service, node=self.node1, container_name="multi-app-replica-n1", status="RUNNING",
        )
        node1_caddy = generate_node_caddyfile(self.node1)
        self.assertIn("reverse_proxy traefik:80 {", node1_caddy)

    def test_node_service_domains_for_service_returns_node_and_grid_hosts(self):
        hosts = node_service_domains_for_service(self.service)
        self.assertIn("multi-app-node1.trulay.site", hosts)
        self.assertIn("multi-app.node1.trulay.site", hosts)
        self.assertIn("multi-app-grid1.trulay.site", hosts)

    def test_service_ha_counts_canonical_replicas_and_does_not_mark_spawning_as_running(self):
        c1 = MagicMock(id="cid1", name="multi-app")
        c2 = MagicMock(id="cid2", name="multi-app-replica-abc")
        c_rb = MagicMock(id="cid3", name="multi-app-rollback-999")
        mock_client = MagicMock()
        mock_client.containers.list.side_effect = [
            [c1],
            [c1, c2, c_rb],
        ]
        with patch("apps.cloud.docker_client.get_docker_client", return_value=mock_client):
            count = ServiceHAManager()._count_running_containers(self.service)
        self.assertEqual(count, 2)
