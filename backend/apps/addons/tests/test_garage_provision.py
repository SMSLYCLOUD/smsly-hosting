# pylint: disable=invalid-name
"""Garage-backed MINIO addon (MinIO withdrew all public images).

Tests the pure parts: garage.toml rendering and key-output parsing.
The live flow (create/cp/start/layout/key/bucket) is verified against
a real dxflrs/garage container on the VPS before shipping.
"""
from django.test import SimpleTestCase

from apps.addons.services.addon_provisioner import addon_provisioner


class GarageTomlTests(SimpleTestCase):
    def test_toml_renders_ports_and_secrets(self):
        text = addon_provisioner._garage_toml(
            rpc_secret="r" * 64, admin_token="admintoken123", s3_port=9000,
        )
        self.assertIn('rpc_secret = "rrrr', text)
        self.assertIn('admin_token = "admintoken123"', text)
        self.assertIn('api_bind_addr = "[::]:9000"', text)
        self.assertIn("replication_factor = 1", text)
        self.assertIn("[s3_api]", text)
        self.assertIn("[admin]", text)

    def test_ansi_stripped(self):
        dirty = "\x1b[2mKey ID:\x1b[0m  GKabc123"
        self.assertEqual(
            addon_provisioner._strip_ansi(dirty), "Key ID:  GKabc123",
        )
