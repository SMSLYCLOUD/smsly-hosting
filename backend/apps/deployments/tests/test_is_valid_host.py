import os
from unittest import mock

from django.test import TestCase

from apps.deployments.models import PlatformConfig
from apps.core.patching import is_valid_host


class IsValidHostTests(TestCase):
    def setUp(self):
        PlatformConfig.objects.all().delete()
        self.cfg = PlatformConfig.objects.create(
            domain="distinction-lab-c5vxs-7dd94d.grid.smsly.cloud",
            use_ssl=True,
            server_ip="69.164.244.51"
        )

    def test_loopback_and_private_ips_are_valid(self):
        # Loopback
        self.assertTrue(is_valid_host("127.0.0.1"))
        self.assertTrue(is_valid_host("::1"))

        # Private IPs (RFC 1918 / mesh range)
        self.assertTrue(is_valid_host("10.100.0.1"))
        self.assertTrue(is_valid_host("10.100.0.5"))
        self.assertTrue(is_valid_host("172.18.0.5"))
        self.assertTrue(is_valid_host("192.168.1.1"))

    def test_node_host_from_env_is_valid(self):
        with mock.patch.dict(os.environ, {"SMSLY_NODE_HOST": "69.164.244.51"}):
            self.assertTrue(is_valid_host("69.164.244.51"))

        with mock.patch.dict(os.environ, {"SMSLY_NODE_HOST": "209.159.152.123"}):
            self.assertTrue(is_valid_host("209.159.152.123"))

    def test_server_ip_from_platform_config_is_valid(self):
        self.assertTrue(is_valid_host("69.164.244.51"))

    def test_platform_domain_is_valid(self):
        self.assertTrue(is_valid_host("distinction-lab-c5vxs-7dd94d.grid.smsly.cloud"))

    def test_arbitrary_public_ips_and_domains_are_invalid(self):
        self.assertFalse(is_valid_host("8.8.8.8"))
        self.assertFalse(is_valid_host("invalid-domain.com"))
        self.assertFalse(is_valid_host("1.1.1.1"))
