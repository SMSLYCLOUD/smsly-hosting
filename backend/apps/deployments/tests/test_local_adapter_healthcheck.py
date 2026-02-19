"""Unit tests for LocalAdapter healthcheck probe generation."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.cloud.adapters.local import (
    LocalAdapter,
    _build_docker_healthcheck_cmd,
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

    def _build_adapter_with_mock_docker(self):
        docker_client = MagicMock()
        docker_client.api.create_endpoint_config.return_value = {}
        docker_client.api.create_networking_config.return_value = {}

        existing = MagicMock()
        docker_client.containers.get.return_value = existing

        created = MagicMock()
        created.id = "container-id"
        docker_client.containers.create.return_value = created

        adapter = object.__new__(LocalAdapter)
        adapter.mode = "AUTO"
        adapter.docker_client = docker_client
        adapter.k8s_client = None
        adapter.batch_v1 = None
        return adapter, docker_client

    @patch("apps.deployments.models.PlatformConfig.load")
    def test_sets_forwarded_https_headers_when_caddy_terminates_tls(self, mock_load):
        adapter, docker_client = self._build_adapter_with_mock_docker()
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
        self.assertEqual(labels["traefik.http.routers.buyforfront.entrypoints"], "web")
        self.assertEqual(
            labels["traefik.http.routers.buyforfront.middlewares"],
            "buyforfront-forwarded-https",
        )
        self.assertEqual(
            labels[
                "traefik.http.middlewares.buyforfront-forwarded-https.headers.customrequestheaders.X-Forwarded-Proto"
            ],
            "https",
        )
        self.assertEqual(
            labels[
                "traefik.http.middlewares.buyforfront-forwarded-https.headers.customrequestheaders.X-Forwarded-Port"
            ],
            "443",
        )

    @patch("apps.deployments.models.PlatformConfig.load")
    def test_uses_websecure_router_when_direct_traefik_tls_enabled(self, mock_load):
        adapter, docker_client = self._build_adapter_with_mock_docker()
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
        self.assertEqual(labels["traefik.http.routers.buyforfront.entrypoints"], "websecure")
        self.assertEqual(labels["traefik.http.routers.buyforfront.tls"], "true")
        self.assertNotIn("traefik.http.routers.buyforfront.middlewares", labels)
