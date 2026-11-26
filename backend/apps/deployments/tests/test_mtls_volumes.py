"""Unit tests for resolve_spire_volume_name (docker fully mocked, no DB)."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services.mtls_integration import (
    SPIRE_SOCKET_CONTAINER_PATH,
    SPIRE_SVIDS_CONTAINER_PATH,
    get_mtls_volumes,
    resolve_spire_volume_name,
)

# Mirror of the live host layout (2026-09-15): the bare volumes are
# auto-created EMPTY decoys; the real sockets live under project prefixes.
LIVE_VOLUMES = [
    "smsly-hosting_spire-agent-data",
    "smsly-hosting_spire-agent-socket",
    "smsly-hosting_spire-agent-svids",
    "smsly-spire_spire-ecosystem-agent-data",
    "smsly-spire_spire-ecosystem-agent-socket",
    "smsly-spire_spire-ecosystem-agent-svids",
    "smsly-spire_spire-ecosystem-server-data",
    "smsly-spire_spire-server-data",
    "spire-ecosystem-agent-socket",
    "spire-ecosystem-agent-svids",
]


def _client(names):
    client = MagicMock()
    vols = []
    for name in names:
        vol = MagicMock()
        vol.name = name
        vols.append(vol)
    client.volumes.list.return_value = vols
    return client


def _resolve(short_name, names=LIVE_VOLUMES):
    with patch(
        "apps.cloud.docker_client.get_docker_client",
        return_value=_client(names),
    ):
        return resolve_spire_volume_name(short_name)


class TestResolveSpireVolumeName(TestCase):
    def test_ecosystem_decoy_loses_to_namespaced(self):
        """2026-09-15 incident: the bare volume exists but is empty; the
        real socket lives in smsly-spire_*. Must never return the decoy."""
        self.assertEqual(
            _resolve("spire-ecosystem-agent-socket"),
            "smsly-spire_spire-ecosystem-agent-socket",
        )
        self.assertEqual(
            _resolve("spire-ecosystem-agent-svids"),
            "smsly-spire_spire-ecosystem-agent-svids",
        )

    def test_platform_short_still_resolves(self):
        self.assertEqual(
            _resolve("spire-agent-socket"),
            "smsly-hosting_spire-agent-socket",
        )

    def test_unknown_name_returned_unchanged(self):
        self.assertEqual(_resolve("spire-nope-socket"), "spire-nope-socket")

    def test_docker_failure_returns_short_name(self):
        with patch(
            "apps.cloud.docker_client.get_docker_client",
            side_effect=Exception("no docker"),
        ):
            self.assertEqual(
                resolve_spire_volume_name("spire-ecosystem-agent-socket"),
                "spire-ecosystem-agent-socket",
            )

    def test_no_partial_suffix_confusion(self):
        """smsly-spire_spire-ecosystem-agent-socket must NOT match the
        platform short name (and vice versa)."""
        self.assertEqual(
            _resolve(
                "spire-agent-socket",
                ["smsly-spire_spire-ecosystem-agent-socket"],
            ),
            "spire-agent-socket",
        )


class TestGetMtlsVolumes(TestCase):
    def test_mounts_socket_and_svids_read_only_across_management_modes(self):
        for managed_by, exp_socket, exp_svids in (
            ("USER", "smsly-spire_spire-ecosystem-agent-socket", "smsly-spire_spire-ecosystem-agent-svids"),
            ("ECOSYSTEM", "smsly-spire_spire-ecosystem-agent-socket", "smsly-spire_spire-ecosystem-agent-svids"),
            ("PLATFORM", "smsly-hosting_spire-agent-socket", "smsly-hosting_spire-agent-svids"),
        ):
            svc = SimpleNamespace(managed_by=managed_by, name=f"svc-{managed_by.lower()}")
            with patch(
                "apps.cloud.docker_client.get_docker_client",
                return_value=_client(LIVE_VOLUMES),
            ):
                vols = get_mtls_volumes(svc)
            self.assertEqual(
                vols,
                [
                    (exp_socket, SPIRE_SOCKET_CONTAINER_PATH, "ro"),
                    (exp_svids, SPIRE_SVIDS_CONTAINER_PATH, "ro"),
                ],
            )
            self.assertTrue(all(mode == "ro" for _, _, mode in vols))
