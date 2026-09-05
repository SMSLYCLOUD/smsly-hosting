"""Tests for multi-node registry routing (registry_routing.py).

Covers every node type's rewrite expectations:
  - internal refs (registry:5000 / loopback) -> node-routable URL
  - external registries pass through untouched
  - single-host installs (no routable URL configured) are a no-op
  - is_master_registry_ref recognizes all platform-registry addresses
"""
from unittest import mock

from django.test import TestCase

from apps.deployments.services.registry_routing import (
    image_ref_for_internal,
    image_ref_for_node,
    is_master_registry_ref,
    master_registry_node_url,
)


class RegistryRoutingTests(TestCase):
    def _set_env(self, **kwargs):
        return mock.patch.dict('os.environ', kwargs, clear=False)

    def test_mesh_ip_env_resolves(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='10.100.0.1', MASTER_PUBLIC_IP=''):
            self.assertEqual(master_registry_node_url(), '10.100.0.1:5000')

    def test_public_ip_fallback(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='', MASTER_PUBLIC_IP='203.0.113.10'):
            self.assertEqual(master_registry_node_url(), '203.0.113.10:5000')

    def test_no_config_returns_empty(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='', MASTER_PUBLIC_IP='', MASTER_REGISTRY_PUBLIC_URL=''):
            url = master_registry_node_url()
            # No PlatformConfig override in tests -> empty
            self.assertEqual(url, '')

    def test_internal_ref_rewritten_for_node(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='10.100.0.1'):
            self.assertEqual(
                image_ref_for_node('registry:5000/smsly/app:abc123'),
                '10.100.0.1:5000/smsly/app:abc123',
            )
            self.assertEqual(
                image_ref_for_node('127.0.0.1:5000/smsly/app:abc123'),
                '10.100.0.1:5000/smsly/app:abc123',
            )
            self.assertEqual(
                image_ref_for_node('localhost:5000/smsly/app:abc123'),
                '10.100.0.1:5000/smsly/app:abc123',
            )

    def test_no_rewrite_when_no_routable_url(self):
        # Single-host install: no mesh, no public override -> unchanged
        with self._set_env(WIREGUARD_MASTER_MESH_IP='', MASTER_PUBLIC_IP='', MASTER_REGISTRY_PUBLIC_URL=''):
            self.assertEqual(
                image_ref_for_node('registry:5000/smsly/app:abc123'),
                'registry:5000/smsly/app:abc123',
            )

    def test_external_registry_untouched(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='10.100.0.1'):
            self.assertEqual(
                image_ref_for_node('ghcr.io/smsly/app:v1'),
                'ghcr.io/smsly/app:v1',
            )
            self.assertEqual(
                image_ref_for_node('docker.io/library/nginx:latest'),
                'docker.io/library/nginx:latest',
            )

    def test_unqualified_name_untouched(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='10.100.0.1'):
            self.assertEqual(image_ref_for_node('smsly/app:abc'), 'smsly/app:abc')

    def test_node_ref_back_to_internal(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='10.100.0.1'):
            self.assertEqual(
                image_ref_for_internal('10.100.0.1:5000/smsly/app:abc'),
                'registry:5000/smsly/app:abc',
            )
            # Non-matching host passes through
            self.assertEqual(
                image_ref_for_internal('ghcr.io/smsly/app:v1'),
                'ghcr.io/smsly/app:v1',
            )

    def test_is_master_registry_ref_all_addresses(self):
        with self._set_env(WIREGUARD_MASTER_MESH_IP='10.100.0.1'):
            self.assertTrue(is_master_registry_ref('registry:5000/smsly/app:abc'))
            self.assertTrue(is_master_registry_ref('127.0.0.1:5000/smsly/app:abc'))
            self.assertTrue(is_master_registry_ref('10.100.0.1:5000/smsly/app:abc'))
            self.assertFalse(is_master_registry_ref('ghcr.io/smsly/app:v1'))
            self.assertFalse(is_master_registry_ref('evil.example.com:5000/smsly/app:abc'))
