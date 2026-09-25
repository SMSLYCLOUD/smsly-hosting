# pylint: disable=invalid-name
"""Sandboxed containers need explicit DNS (gVisor/Kata swallow 127.0.0.11).

Regression (2026-09-25): every external lookup failed inside runsc
user containers (OTP mail provider unreachable) while identical
runc containers on the same bridge resolved fine. Docker embedded
DNS is unreachable from sandboxed netstacks, so the deploy path
passes explicit resolvers via container ``dns=``.
"""
import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from apps.deployments.services.container_runtime import (
    is_sandboxed_runtime,
    sandbox_dns_servers,
)


class SandboxDnsServersTests(SimpleTestCase):
    def test_public_fallbacks_always_present(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMSLY_RUNSC_DNS", None)
            servers = sandbox_dns_servers()
        self.assertIn("8.8.8.8", servers)
        self.assertIn("1.1.1.1", servers)

    def test_env_override_wins(self):
        with patch.dict(os.environ, {"SMSLY_RUNSC_DNS": "9.9.9.9, 1.0.0.1"}):
            self.assertEqual(sandbox_dns_servers(), ["9.9.9.9", "1.0.0.1"])

    def test_is_sandboxed_runtime(self):
        self.assertTrue(is_sandboxed_runtime("runsc"))
        self.assertTrue(is_sandboxed_runtime("kata-runtime"))
        self.assertFalse(is_sandboxed_runtime("runc"))
        self.assertFalse(is_sandboxed_runtime(None))
        self.assertFalse(is_sandboxed_runtime(""))


class SandboxDnsDbTests(TestCase):
    def test_mesh_ip_first_when_mesh_configured(self):
        from apps.deployments.models.mesh import MeshNetwork, WireGuardPeer

        mesh = MeshNetwork.objects.create(
            name="default",
            subnet="10.100.0.0/24",
            listen_port=51820,
            interface_name="wg0",
            is_active=True,
        )
        WireGuardPeer.objects.create(
            mesh=mesh,
            server=None,
            private_key="x",
            public_key="y",
            wg_address="10.100.0.1",
            endpoint="",
            allowed_ips="10.100.0.1/32",
            is_active=True,
            is_local=True,
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SMSLY_RUNSC_DNS", None)
            servers = sandbox_dns_servers()
        self.assertEqual(servers[0], "10.100.0.1")
