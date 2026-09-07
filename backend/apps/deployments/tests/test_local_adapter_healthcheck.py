"""Unit tests for LocalAdapter healthcheck probe generation."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docker
from django.test import SimpleTestCase

from apps.cloud.adapters.local import (
    LocalAdapter,
    _build_docker_healthcheck_cmd,
    _localhost_free_healthcheck,
    _normalize_health_path,
)


class LocalAdapterHealthcheckCommandTests(SimpleTestCase):
    """Ensure generated probe commands are resilient across base images."""

    def test_normalize_health_path_prefixes_slash(self):
        self.assertEqual(_normalize_health_path("health"), "/health")
        self.assertEqual(_normalize_health_path("/health"), "/health")
        self.assertEqual(_normalize_health_path(""), "/")

    def test_healthcheck_command_has_tool_fallbacks(self):
        cmd = _build_docker_healthcheck_cmd("http://localhost:8000/health", 5)

        self.assertIn("command -v wget", cmd)
        self.assertIn("command -v curl", cmd)
        self.assertIn("command -v python3", cmd)
        self.assertIn("command -v python", cmd)
        self.assertIn("exit 0", cmd)
        self.assertIn("exit 1", cmd)

    def _build_adapter_with_mock_docker(self, has_old: bool = False):
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}

        existing = None
        if has_old:
            existing = MagicMock()
            docker_client.containers.get.return_value = existing
        else:
            docker_client.containers.get.side_effect = docker.errors.NotFound("missing")

        created = MagicMock()
        created.id = "container-id"
        docker_client.containers.create.return_value = created

        adapter = object.__new__(LocalAdapter)
        adapter.mode = "AUTO"
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None
        return adapter, docker_client, existing

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_sets_forwarded_https_headers_when_caddy_terminates_tls(self, mock_load, _wait_mock):
        adapter, docker_client, _existing = self._build_adapter_with_mock_docker()
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        with patch.dict(os.environ, {"TRAEFIK_ENABLE_WEBSECURE": "false"}, clear=False):
            adapter._deploy_docker(
                name="buyforfront",
                image="registry:5000/smsly/buyforfront:test",
                env={
                    "PORT": "8000",
                    "PUBLIC_DOMAIN": "buyforfront-0398be.cloud.smsly.cloud",
                },
            )

        labels = docker_client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(labels["traefik.enable"], "true")
        self.assertEqual(labels["traefik.http.routers.buyforfront.entrypoints"], "web")
        self.assertNotIn("traefik.http.routers.buyforfront.tls", labels)
        self.assertNotIn("traefik.http.routers.buyforfront.middlewares", labels)

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_uses_websecure_router_when_direct_traefik_tls_enabled(self, mock_load, _wait_mock):
        adapter, docker_client, _existing = self._build_adapter_with_mock_docker()
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        with patch.dict(os.environ, {"TRAEFIK_ENABLE_WEBSECURE": "true"}, clear=False):
            adapter._deploy_docker(
                name="buyforfront",
                image="registry:5000/smsly/buyforfront:test",
                env={
                    "PORT": "8000",
                    "PUBLIC_DOMAIN": "buyforfront-0398be.cloud.smsly.cloud",
                },
            )

        labels = docker_client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(labels["traefik.enable"], "true")
        self.assertEqual(labels["traefik.http.routers.buyforfront.entrypoints"], "web,websecure")
        self.assertNotIn("traefik.http.routers.buyforfront.tls", labels)
        self.assertEqual(labels["smsly.blue_green.enable_tls"], "True")

    @patch.object(LocalAdapter, "promote_container", return_value="promoted-container-id")
    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_existing_live_container_stages_green_before_promote(
        self,
        mock_load,
        _wait_mock,
        mock_promote,
    ):
        adapter, docker_client, existing = self._build_adapter_with_mock_docker(has_old=True)
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        with patch.dict(os.environ, {"TRAEFIK_ENABLE_WEBSECURE": "false"}, clear=False):
            result = adapter._deploy_docker(
                name="buyforfront",
                image="registry:5000/smsly/buyforfront:test",
                env={
                    "PORT": "8000",
                    "PUBLIC_DOMAIN": "buyforfront-0398be.cloud.smsly.cloud",
                },
            )

        create_kwargs = docker_client.containers.create.call_args.kwargs
        labels = create_kwargs["labels"]
        self.assertTrue(create_kwargs["name"].startswith("buyforfront-green-"))
        self.assertEqual(labels["traefik.enable"], "false")
        self.assertNotIn("traefik.http.routers.buyforfront.rule", labels)
        existing.stop.assert_not_called()
        existing.remove.assert_not_called()
        mock_promote.assert_called_once()
        self.assertEqual(result, "promoted-container-id")

    @patch.object(LocalAdapter, "promote_container", side_effect=RuntimeError("promote failed"))
    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_existing_live_container_not_stopped_if_promote_fails(
        self,
        mock_load,
        _wait_mock,
        _mock_promote,
    ):
        adapter, _docker_client, existing = self._build_adapter_with_mock_docker(has_old=True)
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        with patch.dict(os.environ, {"TRAEFIK_ENABLE_WEBSECURE": "false"}, clear=False):
            with self.assertRaises(RuntimeError):
                adapter._deploy_docker(
                    name="buyforfront",
                    image="registry:5000/smsly/buyforfront:test",
                    env={
                        "PORT": "8000",
                        "PUBLIC_DOMAIN": "buyforfront-0398be.cloud.smsly.cloud",
                    },
                )

        existing.stop.assert_not_called()
        existing.remove.assert_not_called()

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_command_override_is_passed_to_container_create(self, mock_load, _wait_mock):
        adapter, docker_client, _existing = self._build_adapter_with_mock_docker()
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        adapter._deploy_docker(
            name="ai-router",
            image="ghcr.io/berriai/litellm:main-stable",
            env={
                "PORT": "4000",
                "PUBLIC_DOMAIN": "ai-router.example.com",
            },
            command="--model ollama/phi3 --api_base http://ollama:11434 --port 4000",
        )

        create_kwargs = docker_client.containers.create.call_args.kwargs
        self.assertEqual(
            create_kwargs["command"],
            ["--model", "ollama/phi3", "--api_base", "http://ollama:11434", "--port", "4000"],
        )

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_ai_router_api_base_adds_rewrite_middleware(self, mock_load, _wait_mock):
        adapter, docker_client, _existing = self._build_adapter_with_mock_docker()
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        adapter._deploy_docker(
            name="ai-router",
            image="ghcr.io/berriai/litellm:main-stable",
            env={
                "PORT": "4000",
                "PUBLIC_DOMAIN": "ai-router.example.com",
                "AI_ROUTER_API_BASE": "/api/v1",
                "LITELLM_MASTER_KEY": "test-key",
            },
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(labels["traefik.http.routers.ai-router.middlewares"], "ai-router-api-base")
        self.assertEqual(
            labels["traefik.http.middlewares.ai-router-api-base.replacepathregex.regex"],
            r"^/api/v1/?(.*)$",
        )
        self.assertEqual(
            labels["traefik.http.middlewares.ai-router-api-base.replacepathregex.replacement"],
            "/v1/$1",
        )

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    def test_promote_container_rebuilds_ai_router_rewrite_middleware(self, _wait_mock):
        adapter = object.__new__(LocalAdapter)
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None

        green = MagicMock()
        green.name = "ai-router-green-abc123"
        green.id = "green-id"
        green.labels = {
            "smsly.blue_green.is_public": "True",
            "smsly.blue_green.port": "4000",
            "smsly.blue_green.host_rule": "Host(`ai-router.example.com`)",
            "traefik.enable": "false",
        }
        green.attrs = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {
                "Env": [
                    "AI_ROUTER_API_BASE=/api/v1",
                    "LITELLM_MASTER_KEY=test-key",
                ],
                "Cmd": None,
                "Entrypoint": None,
                "Healthcheck": None,
            },
            "HostConfig": {
                "Binds": None,
                "RestartPolicy": {},
            },
        }
        green.image.tags = ["ghcr.io/berriai/litellm:main-stable"]

        old_live = MagicMock()
        promoted = MagicMock()
        promoted.id = "promoted-id"
        docker_client.containers.create.return_value = promoted
        docker_client.containers.get.side_effect = lambda value: (
            green if value == "green-id" else old_live
        )
        docker_client.networks.get.return_value = MagicMock()

        result = adapter.promote_container("ai-router", "green-id")

        self.assertEqual(result, "promoted-id")
        labels = docker_client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(labels["traefik.http.routers.ai-router.middlewares"], "ai-router-api-base")
        self.assertEqual(
            labels["traefik.http.middlewares.ai-router-api-base.replacepathregex.regex"],
            r"^/api/v1/?(.*)$",
        )
        self.assertEqual(
            labels["traefik.http.middlewares.ai-router-api-base.replacepathregex.replacement"],
            "/v1/$1",
        )

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    def test_promote_container_injects_traefik_loadbalancer_healthchecks(self, _wait_mock):
        adapter = object.__new__(LocalAdapter)
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None

        green = MagicMock()
        green.name = "buyforfront-green-e844e7"
        green.id = "green-id"
        green.labels = {
            "smsly.blue_green.is_public": "True",
            "smsly.blue_green.port": "8000",
            "smsly.blue_green.host_rule": "Host(`buyforfront.example.com`)",
            "smsly.blue_green.hc_path": "/healthz",
            "smsly.blue_green.hc_interval": "10",
            "smsly.blue_green.hc_timeout": "5",
            "traefik.enable": "false",
        }
        green.attrs = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {
                "Env": [],
                "Cmd": None,
                "Entrypoint": None,
                "Healthcheck": None,
            },
            "HostConfig": {
                "Binds": None,
                "RestartPolicy": {},
            },
        }
        green.image.tags = ["registry:5000/smsly/buyforfront:test"]

        old_live = MagicMock()
        promoted = MagicMock()
        promoted.id = "promoted-id"
        docker_client.containers.create.return_value = promoted
        docker_client.containers.get.side_effect = lambda value: (
            green if value == "green-id" else old_live
        )
        docker_client.networks.get.return_value = MagicMock()

        result = adapter.promote_container("buyforfront", "green-id")

        self.assertEqual(result, "promoted-id")
        labels = docker_client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(labels["traefik.http.services.buyforfront.loadbalancer.healthcheck.path"], "/healthz")
        self.assertEqual(labels["traefik.http.services.buyforfront.loadbalancer.healthcheck.interval"], "10s")
        self.assertEqual(labels["traefik.http.services.buyforfront.loadbalancer.healthcheck.timeout"], "5s")

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    def test_initial_deploy_injects_traefik_loadbalancer_healthchecks(self, mock_load, _wait_mock):
        adapter, docker_client, _existing = self._build_adapter_with_mock_docker()
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        adapter._deploy_docker(
            name="buyforfront",
            image="registry:5000/smsly/buyforfront:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "buyforfront.example.com",
            },
            healthcheck={
                "path": "/healthz",
                "port": 8000,
                "interval": 15,
                "timeout": 5,
                "retries": 3,
            }
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]
        self.assertEqual(labels["traefik.http.services.buyforfront.loadbalancer.healthcheck.path"], "/healthz")
        self.assertEqual(labels["traefik.http.services.buyforfront.loadbalancer.healthcheck.interval"], "15s")
        self.assertEqual(labels["traefik.http.services.buyforfront.loadbalancer.healthcheck.timeout"], "5s")

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    @patch("apps.deployments.models.Service.objects.filter")
    def test_neutralizes_parent_labels_for_preview_environments(self, mock_filter, mock_load, _wait_mock):
        adapter, docker_client, _existing = self._build_adapter_with_mock_docker()
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        # Mock Service object returning a preview service with a parent service
        mock_parent = SimpleNamespace(name="parent-service")
        mock_svc = SimpleNamespace(is_preview=True, parent_service=mock_parent)
        mock_filter.return_value.first.return_value = mock_svc

        adapter._deploy_docker(
            name="preview-service",
            image="registry:5000/smsly/parent-service:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "preview-service.example.com",
            },
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]
        # Ensure parent router labels are neutralized
        self.assertEqual(labels["traefik.http.routers.parent-service.rule"], "Host(`disabled.localhost`)")
        self.assertEqual(labels["traefik.http.routers.parent-service.entrypoints"], "web")
        self.assertEqual(labels["traefik.http.routers.parent-service.priority"], "0")
        self.assertEqual(labels["traefik.http.services.parent-service.loadbalancer.server.port"], "0")

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.Service.objects.filter")
    def test_promote_container_neutralizes_parent_labels_for_preview(self, mock_filter, _wait_mock):
        adapter = object.__new__(LocalAdapter)
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None

        green = MagicMock()
        green.name = "preview-service-green-abc123"
        green.id = "green-id"
        green.labels = {
            "smsly.blue_green.is_public": "True",
            "smsly.blue_green.port": "8000",
            "smsly.blue_green.host_rule": "Host(`preview-service.example.com`)",
            "traefik.enable": "false",
        }
        green.attrs = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {
                "Env": [],
                "Cmd": None,
                "Entrypoint": None,
                "Healthcheck": None,
            },
            "HostConfig": {
                "Binds": None,
                "RestartPolicy": {},
            },
        }
        green.image.tags = ["registry:5000/smsly/parent-service:test"]

        old_live = MagicMock()
        promoted = MagicMock()
        promoted.id = "promoted-id"
        docker_client.containers.create.return_value = promoted
        docker_client.containers.get.side_effect = lambda value: (
            green if value == "green-id" else old_live
        )
        docker_client.networks.get.return_value = MagicMock()

        # Mock Service object returning a preview service with a parent service
        mock_parent = SimpleNamespace(name="parent-service")
        mock_svc = SimpleNamespace(is_preview=True, parent_service=mock_parent)
        mock_filter.return_value.first.return_value = mock_svc

        result = adapter.promote_container("preview-service", "green-id")

        self.assertEqual(result, "promoted-id")
        labels = docker_client.containers.create.call_args.kwargs["labels"]
        # Ensure parent router labels are neutralized
        self.assertEqual(labels["traefik.http.routers.parent-service.rule"], "Host(`disabled.localhost`)")
        self.assertEqual(labels["traefik.http.routers.parent-service.entrypoints"], "web")
        self.assertEqual(labels["traefik.http.routers.parent-service.priority"], "0")
        self.assertEqual(labels["traefik.http.services.parent-service.loadbalancer.server.port"], "0")

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.PlatformConfig.load")
    @patch("apps.deployments.models.Service.objects.filter")
    def test_local_preview_disables_traefik_on_initial_deploy(self, mock_filter, mock_load, _wait_mock):
        adapter, docker_client, _existing = self._build_adapter_with_mock_docker()
        mock_load.return_value = SimpleNamespace(use_ssl=True)

        # Mock Service object returning a local preview service
        mock_server = SimpleNamespace(is_primary=True)
        mock_parent = SimpleNamespace(name="parent-service")
        mock_svc = SimpleNamespace(is_preview=True, parent_service=mock_parent, server=mock_server)
        mock_filter.return_value.first.return_value = mock_svc

        adapter._deploy_docker(
            name="preview-service",
            image="registry:5000/smsly/parent-service:test",
            env={
                "PORT": "8000",
                "PUBLIC_DOMAIN": "preview-service.example.com",
            },
        )

        labels = docker_client.containers.create.call_args.kwargs["labels"]
        # Ensure Traefik is completely disabled and all Traefik labels are removed
        self.assertEqual(labels.get("traefik.enable"), "false")
        for k in labels:
            if k.startswith("traefik.") and k != "traefik.enable":
                self.fail(f"Found unexpected Traefik label: {k}")

    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    @patch("apps.deployments.models.Service.objects.filter")
    def test_local_preview_disables_traefik_on_promote(self, mock_filter, _wait_mock):
        adapter = object.__new__(LocalAdapter)
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None

        green = MagicMock()
        green.name = "preview-service-green-abc123"
        green.id = "green-id"
        green.labels = {
            "smsly.blue_green.is_public": "True",
            "smsly.blue_green.port": "8000",
            "smsly.blue_green.host_rule": "Host(`preview-service.example.com`)",
            "traefik.enable": "false",
        }
        green.attrs = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {
                "Env": [],
                "Cmd": None,
                "Entrypoint": None,
                "Healthcheck": None,
            },
            "HostConfig": {
                "Binds": None,
                "RestartPolicy": {},
            },
        }
        green.image.tags = ["registry:5000/smsly/parent-service:test"]

        old_live = MagicMock()
        promoted = MagicMock()
        promoted.id = "promoted-id"
        docker_client.containers.create.return_value = promoted
        docker_client.containers.get.side_effect = lambda value: (
            green if value == "green-id" else old_live
        )
        docker_client.networks.get.return_value = MagicMock()

        # Mock Service object returning a local preview service
        mock_server = SimpleNamespace(is_primary=True)
        mock_parent = SimpleNamespace(name="parent-service")
        mock_svc = SimpleNamespace(is_preview=True, parent_service=mock_parent, server=mock_server)
        mock_filter.return_value.first.return_value = mock_svc

        result = adapter.promote_container("preview-service", "green-id")

        self.assertEqual(result, "promoted-id")
        labels = docker_client.containers.create.call_args.kwargs["labels"]
        # Ensure Traefik is completely disabled and all Traefik labels are removed
        self.assertEqual(labels.get("traefik.enable"), "false")
        for k in labels:
            if k.startswith("traefik.") and k != "traefik.enable":
                self.fail(f"Found unexpected Traefik label: {k}")
class LocalhostHealthcheckRewriteTests(SimpleTestCase):
    """Inherited image probes using the hostname localhost fail on
    IPv6-first containers even when the app is healthy."""

    def test_rewrites_localhost_to_loopback_ip(self):
        fixed = _localhost_free_healthcheck(
            {
                "Test": [
                    "CMD-SHELL",
                    "wget -qO- http://localhost:3000/api/health || "
                    "wget -qO- http://localhost:3000/health || exit 1",
                ],
                "Interval": 30000000000,
                "Timeout": 10000000000,
                "Retries": 3,
                "StartPeriod": 40000000000,
            }
        )
        assert fixed is not None
        # NOTE: docker.types.Healthcheck preserves the capitalized API
        # field names (Test/Interval/...), exactly as the daemon expects.
        self.assertEqual(
            fixed["Test"],
            [
                "CMD-SHELL",
                "wget -qO- http://127.0.0.1:3000/api/health || "
                "wget -qO- http://127.0.0.1:3000/health || exit 1",
            ],
        )
        self.assertEqual(fixed["Interval"], 30000000000)
        self.assertEqual(fixed["Timeout"], 10000000000)
        self.assertEqual(fixed["Retries"], 3)
        self.assertEqual(fixed["StartPeriod"], 40000000000)

    def test_leaves_loopback_ip_probe_untouched(self):
        self.assertIsNone(
            _localhost_free_healthcheck(
                {"Test": ["CMD-SHELL", "wget -qO- http://127.0.0.1:3000/health || exit 1"]}
            )
        )

    def test_leaves_explicit_none_untouched(self):
        self.assertIsNone(_localhost_free_healthcheck({"Test": ["NONE"]}))

    def test_leaves_missing_or_invalid_untouched(self):
        self.assertIsNone(_localhost_free_healthcheck(None))
        self.assertIsNone(_localhost_free_healthcheck({}))
        self.assertIsNone(_localhost_free_healthcheck({"Test": []}))

    def test_missing_timing_keys_fall_back_to_defaults(self):
        fixed = _localhost_free_healthcheck(
            {"Test": ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]}
        )
        assert fixed is not None
        self.assertEqual(fixed["Interval"], 30_000_000_000)
        self.assertEqual(fixed["Timeout"], 10_000_000_000)
        self.assertEqual(fixed["Retries"], 3)
