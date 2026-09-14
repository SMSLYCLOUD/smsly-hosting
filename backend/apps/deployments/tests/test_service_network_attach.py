"""Unit tests for scoped-network attach helpers (docker fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services import network_scope as ns


def _service(name="smsly-identity-service"):
    svc = MagicMock()
    svc.name = name
    svc.project = MagicMock()
    return svc


def _container(networks):
    container = MagicMock()
    container.id = "cid1234567890"
    container.attrs = {"NetworkSettings": {"Networks": networks}}
    return container


class TestAttachContainerToServiceNetwork(TestCase):
    def _client(self, container):
        client = MagicMock()
        client.containers.get.return_value = container
        return client

    @patch("apps.deployments.services.network_scope.docker.from_env")
    @patch("apps.deployments.models.network_scope.ScopedNetwork")
    @patch("apps.deployments.services.network_scope.ensure_scoped_network")
    def test_connects_with_aliases_when_missing(
        self, mock_ensure, mock_scoped, mock_from_env
    ):
        mock_scoped.resolve_network_name.return_value = "smsly-net-96e85eee"
        mock_scoped.resolve_network_config.return_value = {"name": "smsly-net-96e85eee"}
        container = _container({"smsly-net": {}})
        net = MagicMock()
        client = self._client(container)
        client.networks.get.return_value = net
        mock_from_env.return_value = client

        self.assertTrue(ns.attach_container_to_service_network(_service(), "cid1234567890"))
        net.connect.assert_called_once_with(
            container,
            aliases=["smsly-identity-service", "smsly-identity-service.default.internal"],
        )

    @patch("apps.deployments.services.network_scope.docker.from_env")
    @patch("apps.deployments.models.network_scope.ScopedNetwork")
    @patch("apps.deployments.services.network_scope.ensure_scoped_network")
    def test_noop_when_already_attached(
        self, mock_ensure, mock_scoped, mock_from_env
    ):
        mock_scoped.resolve_network_name.return_value = "smsly-net-96e85eee"
        mock_scoped.resolve_network_config.return_value = {"name": "smsly-net-96e85eee"}
        container = _container({"smsly-net-96e85eee": {}})
        net = MagicMock()
        client = self._client(container)
        client.networks.get.return_value = net
        mock_from_env.return_value = client

        self.assertTrue(ns.attach_container_to_service_network(_service(), "cid1234567890"))
        net.connect.assert_not_called()

    @patch("apps.deployments.services.network_scope.docker.from_env")
    @patch("apps.deployments.models.network_scope.ScopedNetwork")
    def test_skips_global_network(self, mock_scoped, mock_from_env):
        mock_scoped.resolve_network_name.return_value = "smsly-net"
        client = MagicMock()
        mock_from_env.return_value = client

        self.assertTrue(ns.attach_container_to_service_network(_service(), "cid1"))
        client.containers.get.assert_not_called()

    @patch("apps.deployments.services.network_scope.docker.from_env")
    def test_missing_container_is_noop(self, mock_from_env):
        import docker

        client = MagicMock()
        client.containers.get.side_effect = docker.errors.NotFound("gone")
        mock_from_env.return_value = client
        with patch(
            "apps.deployments.models.network_scope.ScopedNetwork"
        ) as mock_scoped:
            mock_scoped.resolve_network_name.return_value = "smsly-net-96e85eee"
            mock_scoped.resolve_network_config.return_value = {"name": "smsly-net-96e85eee"}
            with patch(
                "apps.deployments.services.network_scope.ensure_scoped_network"
            ):
                self.assertTrue(
                    ns.attach_container_to_service_network(_service(), "gone")
                )


class TestAttachContainerToPlatformBridge(TestCase):
    @patch("apps.deployments.services.network_scope.docker.from_env")
    @patch("apps.deployments.services.network_scope.ensure_platform_bridge")
    def test_connects_with_aliases_when_missing(
        self, mock_bridge, mock_from_env
    ):
        mock_bridge.return_value = "smsly-platform-net"
        container = _container({"smsly-net": {}})
        net = MagicMock()
        client = MagicMock()
        client.containers.get.return_value = container
        client.networks.get.return_value = net
        mock_from_env.return_value = client

        self.assertTrue(ns.attach_container_to_platform_bridge("cid1234567890", "svc-a"))
        net.connect.assert_called_once_with(
            container, aliases=["svc-a", "svc-a.default.internal"]
        )
