"""Unit tests for SpawningService local destroy (Docker SDK mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

import docker as docker_lib

from apps.deployments.services.spawning_service import SpawningService


def _replica(name="api-replica-abc123"):
    r = MagicMock()
    r.node = None
    r.container_name = name
    r.status = "RUNNING"
    return r


class TestDestroyLocal(TestCase):
    @patch("docker.from_env")
    def test_destroy_local_stops_removes_and_marks(self, mock_from_env):
        container = MagicMock()
        client = MagicMock()
        client.containers.get.return_value = container
        mock_from_env.return_value = client
        replica = _replica()

        SpawningService().destroy(replica)

        client.containers.get.assert_called_once_with("api-replica-abc123")
        container.stop.assert_called_once_with(timeout=15)
        container.remove.assert_called_once_with(force=True)
        self.assertEqual(replica.status, "DESTROYED")
        self.assertIsNotNone(replica.destroyed_at)
        replica.save.assert_called_once()

    @patch("docker.from_env")
    def test_destroy_local_missing_container_still_marks(self, mock_from_env):
        client = MagicMock()
        client.containers.get.side_effect = docker_lib.errors.NotFound("gone")
        mock_from_env.return_value = client
        replica = _replica()

        SpawningService().destroy(replica)

        container = client.containers.get.return_value
        container.stop.assert_not_called()
        self.assertEqual(replica.status, "DESTROYED")
        replica.save.assert_called_once()

    @patch("docker.from_env")
    def test_destroy_local_docker_down_still_marks(self, mock_from_env):
        mock_from_env.side_effect = Exception("no daemon")
        replica = _replica()

        SpawningService().destroy(replica)

        self.assertEqual(replica.status, "DESTROYED")
        replica.save.assert_called_once()
