"""Targeted regression tests for Batch E & F fixes."""

import ast
import os
import tempfile
from unittest.mock import MagicMock, patch

import docker
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.cloud.adapters.local import LocalAdapter
from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.models.servers import ManagedServer
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.models.updates import PlatformUpdate
from apps.deployments.services import caddy_manager
from apps.deployments.services.caddy_manager.tls import _generate_selfsigned_cert
from apps.deployments.services.edge_shield import _format_ds
from apps.deployments.services.fleet_orchestrator import FleetOrchestrator
from apps.deployments.services.node_scorer import NodeScorer
from apps.deployments.services.transfer_service import ServerTransferService

User = get_user_model()


class ComposeStagingUrlTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="comp-stage", password="x")
        self.provider = CloudProvider.objects.create(
            name="comp-local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="comp-svc",
            owner=self.user,
            provider=self.provider,
            deploy_mode="COMPOSE",
            is_public=True,
            public_domain="comp.example.com",
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.BUILDING,
            commit_hash="deadbeef1234",
        )

    @patch("apps.deployments.tasks.deploy.health._wait_for_local_route_ready", return_value=True)
    @patch("apps.deployments.tasks.deploy.health._wait_for_local_container_healthy", return_value=True)
    @patch("apps.deployments.tasks.deploy.health._local_route_timeout_seconds", return_value=45)
    @patch("apps.deployments.tasks.deploy.health._local_container_timeout_seconds", return_value=15)
    @patch("apps.deployments.tasks.deploy.deploy_container._post_deploy_success")
    @patch("apps.deployments.tasks.deploy.deploy_container._regenerate_caddyfile")
    @patch("apps.deployments.tasks.deploy.deploy_container._cancel_previous_staged")
    def test_staged_compose_sets_staging_url_and_passes_route_timeout(
        self,
        _mock_cancel,
        _mock_regen,
        _mock_post,
        _mock_ct,
        _mock_rt,
        _mock_hc,
        mock_route_ready,
    ):
        from apps.deployments.tasks.deploy.deploy_container import _deploy_container

        _deploy_container(self.deployment, self.provider, "compose:comp-svc", staged_only=True)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, Deployment.Status.STAGED)
        self.assertTrue(self.deployment.staging_url)
        mock_route_ready.assert_called_once()
        kwargs = mock_route_ready.call_args.kwargs
        self.assertEqual(kwargs["timeout_seconds"], 45)
        self.assertTrue(kwargs["host_override"])


class GvisorExtraHostsMergeTests(SimpleTestCase):
    def test_runsc_merges_addon_extra_hosts_with_host_gateway(self):
        adapter = LocalAdapter.__new__(LocalAdapter)
        adapter.docker_client = MagicMock()
        adapter._service_id = ""
        adapter._resolve_network_name = MagicMock(return_value="smsly-net")
        adapter._apply_egress_restrictions = MagicMock()
        adapter._get_traefik_labels = MagicMock(return_value={})
        adapter._apply_router_special_labels = MagicMock()
        adapter._wait_container_healthy = MagicMock(return_value=True)
        adapter.docker_client.containers.get.side_effect = docker.errors.NotFound("nope")
        adapter.docker_client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        with patch("apps.deployments.services.container_runtime.get_runtime_for_container", return_value="runsc"), \
             patch("apps.deployments.services.container_runtime.is_sandboxed_runtime", return_value=True), \
             patch("apps.deployments.services.container_runtime.sandbox_dns_servers", return_value=["8.8.8.8"]), \
             patch("apps.deployments.services.container_runtime.ensure_sandbox_resolv_conf", return_value=""), \
             patch("apps.core.crypto_daemon.needs_host_gateway", return_value=True), \
             patch("apps.deployments.services.container_refresh._resolve_gvisor_extra_hosts", return_value=["pg-1:172.18.0.5"]):
            adapter._deploy_docker("my-svc", "my-img:latest", {"PUBLIC_DOMAIN": "app.example.com"})

        create_kwargs = adapter.docker_client.containers.create.call_args.kwargs
        extra_hosts = create_kwargs["extra_hosts"]
        self.assertIn("pg-1:172.18.0.5", extra_hosts)
        self.assertIn("host.docker.internal:host-gateway", extra_hosts)


class ServiceHAPassAlertTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ha-user", password="x")
        self.service = Service.objects.create(name="ha-svc", owner=self.user)
        self.deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash="abc123",
        )

    @patch("apps.core.tasks.alerts.alert_user_task.delay")
    @patch("apps.deployments.services.service_ha.ServiceHAManager")
    def test_service_ha_pass_alerts_with_real_deployment_id(self, mock_mgr_cls, mock_alert_delay):
        from apps.deployments.tasks.service_ha import service_ha_pass

        mock_mgr_cls.return_value.run_ha_pass.return_value = {
            "checked": 2,
            "escalations": 0,
            "remote_failovers": 0,
            "node_failovers": 1,
            "local_reconciles": 0,
            "alerts": ["node-1 failed over"],
        }

        res = service_ha_pass()

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["node_failovers"], 1)
        mock_alert_delay.assert_called_once_with(
            deployment_id=str(self.deployment.id),
            error_message="HA: node-1 failed over",
        )


class CaddyReloadFailureTests(SimpleTestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.caddyfile = os.path.join(self._tmpdir.name, "Caddyfile")
        self.token_cache = os.path.join(self._tmpdir.name, ".cloudflare_token_cache")
        self.token_file = os.path.join(self._tmpdir.name, ".cloudflare_token")
        self.clear_file = os.path.join(self._tmpdir.name, ".cloudflare_token_clear")
        self.valid_content = (
            "(secure_headers) {\n    header X-Content-Type-Options nosniff\n}\n"
            "(coraza_waf) {\n}\n"
            ":80 {\n    import secure_headers\n    reverse_proxy localhost:8090\n}\n"
        )

    def test_apply_caddyfile_fails_when_docker_reload_exits_nonzero(self):
        with patch.object(caddy_manager.apply, "CADDY_CONFIG_DIR", self._tmpdir.name), \
             patch.object(caddy_manager.apply, "CADDY_FILE_PATH", self.caddyfile), \
             patch.object(caddy_manager.apply, "CADDY_TOKEN_CACHE", self.token_cache), \
             patch.object(caddy_manager.apply, "CADDY_TOKEN_FILE", self.token_file), \
             patch.object(caddy_manager.apply, "CADDY_TOKEN_CLEAR_FILE", self.clear_file), \
             patch.object(caddy_manager.apply, "caddy_disabled_mode", return_value=False), \
             patch.object(caddy_manager.apply, "_last_caddy_reload_ts", 0.0), \
             patch.object(caddy_manager.apply, "_last_caddy_content_hash", ""), \
             patch.object(caddy_manager.apply, "validate_service_routes_do_not_hit_control_plane", return_value=[]), \
             patch.object(caddy_manager.apply, "subprocess") as mock_sub:
            mock_sub.TimeoutExpired = TimeoutError
            mock_sub.run.return_value.returncode = 1
            mock_sub.run.return_value.stderr = "syntax error line 3"
            result = caddy_manager.apply_caddyfile(self.valid_content)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reload_error"], "syntax error line 3")

    def test_apply_caddyfile_succeeds_when_docker_cli_missing_for_host_watcher(self):
        with patch.object(caddy_manager.apply, "CADDY_CONFIG_DIR", self._tmpdir.name), \
             patch.object(caddy_manager.apply, "CADDY_FILE_PATH", self.caddyfile), \
             patch.object(caddy_manager.apply, "CADDY_TOKEN_CACHE", self.token_cache), \
             patch.object(caddy_manager.apply, "CADDY_TOKEN_FILE", self.token_file), \
             patch.object(caddy_manager.apply, "CADDY_TOKEN_CLEAR_FILE", self.clear_file), \
             patch.object(caddy_manager.apply, "caddy_disabled_mode", return_value=False), \
             patch.object(caddy_manager.apply, "_last_caddy_reload_ts", 0.0), \
             patch.object(caddy_manager.apply, "_last_caddy_content_hash", ""), \
             patch.object(caddy_manager.apply, "validate_service_routes_do_not_hit_control_plane", return_value=[]), \
             patch.object(caddy_manager.apply, "subprocess") as mock_sub:
            mock_sub.TimeoutExpired = TimeoutError
            mock_sub.run.side_effect = FileNotFoundError("no docker")
            result = caddy_manager.apply_caddyfile(self.valid_content)

        self.assertTrue(result["ok"])


class CaddyIpKeyPermissionTests(SimpleTestCase):
    def test_ip_key_mode_is_not_world_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            crt = os.path.join(tmpdir, "ip.crt")
            key = os.path.join(tmpdir, "ip.key")
            _generate_selfsigned_cert(crt, key, "93.184.216.34")
            self.assertEqual(os.stat(crt).st_mode & 0o777, 0o644)
            self.assertEqual(os.stat(key).st_mode & 0o777, 0o640)


class EnvRemapJsonSerializationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="remap-u", password="x")
        self.service = Service.objects.create(name="remap-svc", owner=self.user)
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type="SERVICE",
            service=self.service,
            source_server_ip="10.0.0.1",
            target_server_ip="10.0.0.2",
            metadata={"pre_transfer_env_vars": {"REDIS_URL": "redis://old:6379/0"}},
        )

    def test_remap_and_revert_scripts_parse_and_serialize_json_string(self):
        svc = ServerTransferService(self.transfer)
        scripts = []
        svc._find_remote_backend_container = MagicMock(return_value="smsly-hosting-backend-1")
        svc._log = MagicMock()

        def capture_exec(code):
            scripts.append(code)
            ast.parse(code)
            return {"stdout": "", "stderr": "", "returncode": 0}

        svc._exec_on_target = capture_exec
        svc._remap_target_platform_env()
        svc._revert_target_platform_env()

        self.assertEqual(len(scripts), 2)
        for script in scripts:
            self.assertNotIn("json.loads({", script)


class EdgeShieldAndNodeHelpersTests(TestCase):
    def test_format_ds_orders_fields_correctly(self):
        ds = _format_ds({
            "key_tag": 2371,
            "algorithm": 13,
            "digest_type": 2,
            "digest": "ABC123",
        })
        self.assertEqual(ds, "2371 13 2 ABC123")

    def test_node_scorer_ip_validation_blocks_promql_injection(self):
        ok_node = MagicMock(wg_address="10.100.0.2", private_ip="", host="1.2.3.4")
        bad_node = MagicMock(wg_address='10.0.0.1",job="x', private_ip="", host="")
        self.assertEqual(NodeScorer._node_ip(ok_node), "10.100.0.2")
        self.assertEqual(NodeScorer._node_ip(bad_node), "")

    def test_fleet_orchestrator_queries_non_primary_online_nodes(self):
        user = User.objects.create_user(username="fleet-u", password="x")
        primary = ManagedServer.objects.create(
            owner=user, name="master", host="93.184.216.10",
            is_primary=True, status=ManagedServer.Status.ONLINE,
        )
        worker_on = ManagedServer.objects.create(
            owner=user, name="worker-on", host="93.184.216.11",
            is_primary=False, status=ManagedServer.Status.ONLINE,
        )
        worker_off = ManagedServer.objects.create(
            owner=user, name="worker-off", host="93.184.216.12",
            is_primary=False, status=ManagedServer.Status.OFFLINE,
        )
        upd = PlatformUpdate.objects.create(initiated_by="test")
        orch = FleetOrchestrator(upd)
        node_ids = set(orch.nodes.values_list("id", flat=True))
        self.assertNotIn(primary.id, node_ids)
        self.assertIn(worker_on.id, node_ids)
        self.assertNotIn(worker_off.id, node_ids)

    def test_websocket_subprotocol_and_terminal_redaction_regression(self):
        from apps.deployments.consumers.base import get_websocket_subprotocol
        from apps.deployments.consumers.terminal import TerminalConsumer

        scope = {"subprotocols": ["token.smsly_secret_12345", "token"]}
        self.assertEqual(get_websocket_subprotocol(scope), "token")
        redacted = TerminalConsumer._redact_command("export SECRET_KEY=mysecret && PGPASSWORD=pw psql")
        self.assertNotIn("mysecret", redacted)
        self.assertNotIn("pw", redacted)
