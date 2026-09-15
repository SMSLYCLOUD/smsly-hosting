"""Unit tests for EnvoySidecar socket-mount health (Docker mocked, no DB).

Covers the 2026-09-15 SVID-less sidecars: injected before the resolver
fix, they mount the empty bare decoy (or nothing) so SDS can never
reach agent.sock. The repair endpoint recreates exactly these.
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.mtls.services.envoy_sidecar import EnvoySidecar

NAMESPACED = "smsly-spire_spire-ecosystem-agent-socket"
DECOY = "spire-ecosystem-agent-socket"


def _service(name="shop"):
    svc = MagicMock()
    svc.name = name
    return svc


def _container(mounts):
    container = MagicMock()
    container.status = "running"
    container.attrs = {"Mounts": mounts}
    return container


def _client(container):
    client = MagicMock()
    client.containers.get.return_value = container
    return client


def _mounts(source):
    return [
        {"Destination": "/etc/envoy/envoy.yaml", "Source": "/opt/builds/envoy-shop.yaml"},
        {"Destination": "/opt/spire/run", "Source": source},
        {"Destination": "/opt/spire/svids", "Source": "smsly-spire_spire-ecosystem-agent-svids"},
    ]


class SocketMountSourceTests(TestCase):
    def test_finds_docker_py_shape(self):
        self.assertEqual(
            EnvoySidecar._socket_mount_source(_mounts(NAMESPACED)), NAMESPACED)

    def test_finds_inspect_shape(self):
        mounts = [{"Target": "/opt/spire/run", "Name": DECOY}]
        self.assertEqual(EnvoySidecar._socket_mount_source(mounts), DECOY)

    def test_missing_mount_returns_none(self):
        self.assertIsNone(EnvoySidecar._socket_mount_source([]))
        self.assertIsNone(EnvoySidecar._socket_mount_source(None))
        self.assertIsNone(
            EnvoySidecar._socket_mount_source(
                [{"Destination": "/etc/envoy/envoy.yaml", "Source": "x"}]))

    def test_skips_non_dict_entries(self):
        self.assertEqual(
            EnvoySidecar._socket_mount_source(["junk", *_mounts(NAMESPACED)]),
            NAMESPACED)

    def test_normalises_daemon_host_path(self):
        # Live daemons report the host path, not the volume name — the
        # 2026-09-15 repair run proved a raw comparison always mismatches
        # (and would churn-recreate healthy sidecars every run).
        mounts = _mounts(
            "/var/lib/docker/volumes/" + NAMESPACED + "/_data")
        self.assertEqual(
            EnvoySidecar._socket_mount_source(mounts), NAMESPACED)

    def test_normalises_decoy_host_path(self):
        mounts = _mounts(
            "/var/lib/docker/volumes/" + DECOY + "/_data")
        self.assertEqual(
            EnvoySidecar._socket_mount_source(mounts), DECOY)


class CheckSocketMountHealthyTests(TestCase):
    @patch("apps.deployments.services.mtls_integration.resolve_spire_volume_name")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_current_mount_healthy(self, mock_get_client, mock_resolve):
        mock_resolve.return_value = NAMESPACED
        mock_get_client.return_value = _client(_container(_mounts(NAMESPACED)))
        result = EnvoySidecar.check_socket_mount_healthy(_service())
        self.assertTrue(result["healthy"])
        self.assertEqual(result["mounted"], NAMESPACED)

    @patch("apps.deployments.services.mtls_integration.resolve_spire_volume_name")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_decoy_mount_unhealthy(self, mock_get_client, mock_resolve):
        mock_resolve.return_value = NAMESPACED
        mock_get_client.return_value = _client(_container(_mounts(DECOY)))
        result = EnvoySidecar.check_socket_mount_healthy(_service())
        self.assertFalse(result["healthy"])
        self.assertIn("stale", result["reason"])

    @patch("apps.deployments.services.mtls_integration.resolve_spire_volume_name")
    @patch("apps.cloud.docker_client.get_docker_client")
    def test_missing_mount_unhealthy(self, mock_get_client, mock_resolve):
        mock_resolve.return_value = NAMESPACED
        mock_get_client.return_value = _client(_container([]))
        result = EnvoySidecar.check_socket_mount_healthy(_service())
        self.assertFalse(result["healthy"])
        self.assertIn("missing", result["reason"])

    @patch("apps.cloud.docker_client.get_docker_client")
    def test_daemon_down_never_raises(self, mock_get_client):
        mock_get_client.side_effect = Exception("no docker")
        result = EnvoySidecar.check_socket_mount_healthy(_service())
        self.assertFalse(result["healthy"])
        self.assertIn("unavailable", result["reason"])
