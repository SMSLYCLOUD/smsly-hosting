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


class GarageKeyResolveTests(SimpleTestCase):
    """_garage_resolve_key: reuse exact, create when absent, fail
    closed on ambiguity (2026-09-26: 10 same-named keys broke
    `bucket allow` with "N matching keys")."""

    def _resolve(self, *exec_results):
        from unittest import mock
        with mock.patch.object(
            addon_provisioner, "_garage_exec",
            side_effect=list(exec_results),
        ):
            return addon_provisioner._garage_resolve_key("c", "mykey")

    def test_reuse_exact_match_no_create(self):
        key_id, secret = self._resolve("Key ID:  GKaaa111\nName:  mykey\n")
        self.assertEqual(key_id, "GKaaa111")
        self.assertIsNone(secret)

    def test_create_when_absent(self):
        err = RuntimeError("garage key info failed: not found")
        created = "Key ID:  GKbbb222\nSecret key:  s3cr3t\n"
        key_id, secret = self._resolve(err, created)
        self.assertEqual(key_id, "GKbbb222")
        self.assertEqual(secret, "s3cr3t")

    def test_ambiguous_fails_closed(self):
        err = RuntimeError("garage key info failed: Bad request: 10 matching keys")
        with self.assertRaisesRegex(RuntimeError, "dedupe"):
            self._resolve(err)
