# pylint: disable=invalid-name
"""Tests for SEC (Issue 54): SSL monitor must not dial internal addresses."""
from unittest.mock import patch, MagicMock

from django.test import TestCase

from apps.cloud.services.ssl_monitor import (
    _is_safe_outbound_target,
    SSLMonitorService,
)


class IsSafeOutboundTargetTests(TestCase):
    def test_loopback_ipv4_rejected(self):
        self.assertFalse(_is_safe_outbound_target("127.0.0.1"))

    def test_loopback_ipv6_rejected(self):
        self.assertFalse(_is_safe_outbound_target("::1"))

    def test_private_rfc1918_rejected(self):
        self.assertFalse(_is_safe_outbound_target("10.0.0.5"))
        self.assertFalse(_is_safe_outbound_target("192.168.1.1"))
        self.assertFalse(_is_safe_outbound_target("172.16.0.1"))

    def test_link_local_rejected(self):
        self.assertFalse(_is_safe_outbound_target("169.254.169.254"))

    def test_multicast_rejected(self):
        self.assertFalse(_is_safe_outbound_target("224.0.0.1"))

    def test_unspecified_rejected(self):
        self.assertFalse(_is_safe_outbound_target("0.0.0.0"))

    def test_empty_host_rejected(self):
        self.assertFalse(_is_safe_outbound_target(""))

    def test_dns_failure_rejected(self):
        with patch("socket.getaddrinfo", side_effect=OSError("nxdomain")):
            self.assertFalse(_is_safe_outbound_target("nope.invalid"))

    def test_public_ip_accepted(self):
        # 1.1.1.1 is a public IP; getaddrinfo is mocked to return it directly
        fake_info = (None, None, None, None, ("1.1.1.1", 0))
        with patch("socket.getaddrinfo", return_value=[fake_info]):
            self.assertTrue(_is_safe_outbound_target("one.one.one.one"))


class SSLMonitorInternalTargetTests(TestCase):
    """The platform-domain and per-domain checks must short-circuit on internal IPs."""

    def test_platform_domain_loopback_is_skipped(self):
        service = SSLMonitorService()
        with patch("apps.cloud.services.ssl_monitor._is_safe_outbound_target", return_value=False), \
                patch("apps.cloud.services.ssl_monitor.socket.socket") as mock_socket:
            service._check_cert_platform("127.0.0.1")
        mock_socket.assert_not_called()

    def test_per_domain_loopback_marks_domain_failed(self):
        service = SSLMonitorService()
        domain_obj = MagicMock()
        domain_obj.domain_name = "169.254.169.254"
        domain_obj.ssl_active = True
        domain_obj.last_error = None
        domain_obj.save = MagicMock()
        with patch("apps.cloud.services.ssl_monitor._is_safe_outbound_target", return_value=False):
            service._check_cert_domain_obj(domain_obj)
        self.assertFalse(domain_obj.ssl_active)
        self.assertIn("internal", (domain_obj.last_error or "").lower())
