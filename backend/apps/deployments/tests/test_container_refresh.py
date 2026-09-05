"""Unit tests for container refresh (Docker SDK fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services.container_refresh import (
    ContainerRefreshError,
    build_refresh_plan,
    recreate_with_fresh_env,
)


def _service():
    svc = MagicMock()
    svc.id = "svc-1"
    svc.name = "api"
    svc.cpu_cores = "2.0"
    svc.memory_mb = 2048
    svc.server_id = None
    ev = MagicMock()
    ev.key = "FOO"
    ev.value = "bar"
    svc.env_vars.all.return_value = [ev]
    return svc


def _container(name="api"):
    c = MagicMock()
    c.id = "abc123def456"
    c.name = name
    c.status = "running"
    c.image.tags = ["registry:5000/api:latest"]
    c.attrs = {
        "Config": {"Image": "registry:5000/api:latest", "Labels": {"smsly.service_id": "svc-1"}},
        "HostConfig": {
            "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
            "Runtime": "runc",
            "Binds": [],
        },
        "NetworkSettings": {"Networks": {"proj-net": {"Aliases": ["api"]}}},
        "Mounts": [{"Type": "volume", "Source": "api-data", "Destination": "/data", "Mode": "rw"}],
    }
    return c


def _client(old):
    client = MagicMock()
    client.containers.get.return_value = old
    client.containers.list.return_value = [old]
    client.api.create_endpoint_config.side_effect = lambda aliases=None: {"aliases": aliases or []}
    new_container = MagicMock()
    new_container.id = "new999"
    new_container.status = "running"
    client.containers.create.return_value = new_container
    return client, new_container


class TestContainerRefresh(TestCase):
    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_dry_run_returns_plan_without_touching(self, mock_from_env, _mock_mtls):
        old = _container()
        client, _ = _client(old)
        mock_from_env.return_value = client
        plan = build_refresh_plan(_service(), old)
        self.assertEqual(plan["image"], "registry:5000/api:latest")
        self.assertEqual(plan["primary_network"], "proj-net")
        self.assertEqual(plan["networks"], ["proj-net"])
        old.stop.assert_not_called()
        client.containers.create.assert_not_called()

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_recreate_order_and_env(self, mock_from_env, _mock_mtls):
        old = _container()
        client, new_container = _client(old)
        mock_from_env.return_value = client
        res = recreate_with_fresh_env(_service())
        self.assertTrue(res["ok"])
        self.assertEqual(res["container"], "api")
        old.stop.assert_called_once()
        old.rename.assert_called_once_with("api-prev")
        create_kwargs = client.containers.create.call_args[1]
        self.assertEqual(create_kwargs["image"], "registry:5000/api:latest")
        self.assertEqual(create_kwargs["environment"]["FOO"], "bar")
        self.assertEqual(create_kwargs["network"], "proj-net")
        self.assertIn("proj-net", create_kwargs["networking_config"])
        self.assertEqual(create_kwargs["labels"], {"smsly.service_id": "svc-1"})
        new_container.start.assert_called_once()
        client.containers.get.return_value.remove.assert_called()

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_create_failure_rolls_back(self, mock_from_env, _mock_mtls):
        old = _container()
        client, _ = _client(old)
        client.containers.create.side_effect = Exception("boom")
        mock_from_env.return_value = client
        with self.assertRaises(ContainerRefreshError):
            recreate_with_fresh_env(_service())
        # rollback renames back: api -> api-prev, then api-prev -> api
        self.assertEqual(old.rename.call_count, 2)
        old.rename.assert_any_call("api-prev")
        old.rename.assert_any_call("api")
        old.start.assert_called_once()

    @patch("docker.from_env")
    def test_no_container_raises(self, mock_from_env):
        client = MagicMock()
        client.containers.list.return_value = []
        client.containers.get.side_effect = Exception("nope")
        mock_from_env.return_value = client
        with self.assertRaises(ContainerRefreshError):
            recreate_with_fresh_env(_service())

    def test_remote_service_refused(self):
        svc = _service()
        svc.server_id = "node-1"
        with self.assertRaises(ContainerRefreshError):
            recreate_with_fresh_env(svc)

    @patch("apps.deployments.services.mtls_integration.get_mtls_env_vars", return_value={})
    @patch("docker.from_env")
    def test_exotic_mount_refuses_before_stop(self, mock_from_env, _mock_mtls):
        old = _container()
        old.attrs["Mounts"] = [{"Type": "tmpfs", "Source": "", "Destination": "/tmp"}]
        client, _ = _client(old)
        mock_from_env.return_value = client
        with self.assertRaises(ContainerRefreshError):
            recreate_with_fresh_env(_service())
        old.stop.assert_not_called()
