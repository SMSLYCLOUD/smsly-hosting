"""Unit tests for EnvoySidecar.remove_orphan_sidecar (Docker mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.mtls.services.envoy_sidecar import EnvoySidecar


def _service(name="shop"):
    svc = MagicMock()
    svc.name = name
    return svc


def _client_with(container=None, get_side_effect=None):
    client = MagicMock()
    if get_side_effect is not None:
        client.containers.get.side_effect = get_side_effect
    else:
        client.containers.get.return_value = container
    return client


class RemoveOrphanSidecarTests(TestCase):
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_created_corpse_removed(self, mock_get_client):
        container = MagicMock()
        container.status = "created"
        mock_get_client.return_value = _client_with(container)

        result = EnvoySidecar.remove_orphan_sidecar(_service())

        self.assertEqual(result["status"], "removed")
        self.assertEqual(result["name"], "envoy-shop")
        container.stop.assert_called_once()
        container.remove.assert_called_once_with(force=True)

    @patch("apps.cloud.docker_client.get_docker_client")
    def test_running_sidecar_never_touched(self, mock_get_client):
        container = MagicMock()
        container.status = "running"
        mock_get_client.return_value = _client_with(container)

        result = EnvoySidecar.remove_orphan_sidecar(_service())

        self.assertEqual(result["status"], "running_kept")
        container.stop.assert_not_called()
        container.remove.assert_not_called()

    @patch("apps.cloud.docker_client.get_docker_client")
    def test_missing_container_reports_not_found(self, mock_get_client):
        mock_get_client.return_value = _client_with(
            get_side_effect=Exception("no such container"))

        result = EnvoySidecar.remove_orphan_sidecar(_service())

        self.assertEqual(result["status"], "not_found")

    @patch("apps.cloud.docker_client.get_docker_client")
    def test_daemon_down_never_raises(self, mock_get_client):
        mock_get_client.side_effect = Exception("no daemon")

        result = EnvoySidecar.remove_orphan_sidecar(_service())

        self.assertEqual(result["status"], "unknown")
