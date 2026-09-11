"""Tests for trusted internal ranges and bridge subnet validation."""

from django.test import SimpleTestCase

from apps.deployments.services.network_scope import (
    TRUSTED_INTERNAL_RANGES,
    allocate_project_subnet,
    is_trusted_internal_subnet,
    validate_internal_subnet,
)


class TrustedInternalRangesTests(SimpleTestCase):
    def test_pool_and_mesh_covered(self):
        self.assertTrue(is_trusted_internal_subnet("172.30.1.0/24"))
        self.assertTrue(is_trusted_internal_subnet("172.30.224.0/24"))
        self.assertTrue(is_trusted_internal_subnet("10.100.0.0/24"))
        self.assertTrue(is_trusted_internal_subnet("172.17.0.0/16"))
        self.assertTrue(is_trusted_internal_subnet("127.0.0.1/32"))

    def test_lan_and_cgnat_covered(self):
        self.assertTrue(is_trusted_internal_subnet("192.168.5.0/24"))
        self.assertTrue(is_trusted_internal_subnet("10.99.0.0/24"))
        self.assertTrue(is_trusted_internal_subnet("100.64.0.0/10"))

    def test_public_rejected(self):
        self.assertFalse(is_trusted_internal_subnet("8.8.8.8"))
        self.assertFalse(is_trusted_internal_subnet("1.2.3.0/24"))
        self.assertFalse(is_trusted_internal_subnet("not-a-cidr"))
        self.assertFalse(is_trusted_internal_subnet(""))

    def test_validate_accepts_and_normalizes(self):
        self.assertEqual(
            validate_internal_subnet("172.30.1.5/24", where="test"),
            "172.30.1.0/24",
        )

    def test_validate_rejects_public(self):
        with self.assertRaises(ValueError):
            validate_internal_subnet("8.8.8.8/32", where="test")

    def test_validate_rejects_garbage(self):
        with self.assertRaises(ValueError):
            validate_internal_subnet("hello", where="test")

    def test_allocate_rejects_untrusted_override(self):
        with self.assertRaises(ValueError):
            allocate_project_subnet(requested="8.8.8.0/24")

    def test_ranges_cover_documented_supernets(self):
        joined = ",".join(TRUSTED_INTERNAL_RANGES)
        for expected in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
            self.assertIn(expected, joined)
