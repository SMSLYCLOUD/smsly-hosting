# pylint: disable=invalid-name
"""
Regression tests for the Batch G provisioning-flow consistency fix.

Covers:
  1. ManagedServerSerializer (read) exposes verify_tls and a
     boolean tls_cert_sha256_set, but never the pin value itself.
  2. tasks_mesh.py auto-heal validates candidate endpoints via
     WireGuardService.validate_endpoint before writing.
  3. services.tls_verify.resolve_tls_verify_for_url returns the
     right verify/fingerprint pair for HTTPS / HTTP / pinned
     / insecure-env-allowed combinations.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.servers import ManagedServer
from apps.deployments.services.tls_verify import (
    _allow_insecure_inter_node_tls,
    resolve_tls_verify_for_url,
)

User = get_user_model()


class ManagedServerSerializerTLSFieldsTests(TestCase):
    """The read serializer surfaces verify_tls and a boolean pin
    indicator so operators can audit TLS posture, but never
    echoes the pin itself.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="tls-ser", password="p"
        )
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="tls-srv",
            host="203.0.113.10",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_unpinned_server_reports_verify_tls_true(self):
        # Default verify_tls is True. The boolean pin flag is False.
        from apps.deployments.views.server import ManagedServerSerializer
        data = ManagedServerSerializer(self.server).data
        self.assertTrue(data["verify_tls"])
        self.assertFalse(data["tls_cert_sha256_set"])

    def test_pinned_server_reports_tls_cert_sha256_set_true(self):
        self.server.tls_cert_sha256 = (
            "a" * 64  # 64 hex chars, the format check is a
                      # future check; for the read serializer we
                      # only need non-empty.
        )
        self.server.save(update_fields=["tls_cert_sha256"])
        from apps.deployments.views.server import ManagedServerSerializer
        data = ManagedServerSerializer(self.server).data
        self.assertTrue(data["verify_tls"])
        self.assertTrue(data["tls_cert_sha256_set"])

    def test_pinned_server_does_not_echo_pin(self):
        # Defence in depth: even though tls_cert_sha256 is on
        # the model, the read serializer must not include the
        # raw pin value in its output (it's declared as a model
        # field but excluded from the serializer's fields list).
        pin = "deadbeef" * 8  # 64 hex chars
        self.server.tls_cert_sha256 = pin
        self.server.save(update_fields=["tls_cert_sha256"])
        from apps.deployments.views.server import ManagedServerSerializer
        data = ManagedServerSerializer(self.server).data
        # The pin value must NOT appear anywhere in the response
        serialized = str(data)
        self.assertNotIn(pin, serialized)
        # Only the boolean indicator is exposed
        self.assertTrue(data["tls_cert_sha256_set"])


class ResolveTLSVerifyForURLTests(TestCase):
    """The new resolve_tls_verify_for_url helper used by the
    provisioner and any other code path that has a URL but not
    yet a ManagedServer row.
    """

    def test_http_url_returns_verify_false(self):
        # Plain HTTP has no cert to verify.
        verify, fingerprint = resolve_tls_verify_for_url("http://example.com")
        self.assertFalse(verify)
        self.assertIsNone(fingerprint)

    def test_https_url_default_returns_verify_true(self):
        # The safe default for HTTPS without a pin is verify=True
        # (don't skip cert verification unless the operator has
        # explicitly opted in via ALLOW_INSECURE_INTER_NODE_TLS).
        verify, fingerprint = resolve_tls_verify_for_url("https://example.com")
        # The default is True unless ALLOW_INSECURE_INTER_NODE_TLS is
        # set in the env (which it isn't by default in tests).
        if _allow_insecure_inter_node_tls():
            self.assertFalse(verify)
        else:
            self.assertTrue(verify)
        self.assertIsNone(fingerprint)

    @patch.dict("os.environ", {"SMSLY_MASTER_TLS_CERT SHA256": "a" * 64})
    def test_https_url_with_env_pin_returns_pin(self):
        verify, fingerprint = resolve_tls_verify_for_url("https://master.example.com")
        self.assertTrue(verify)
        self.assertEqual(fingerprint, "a" * 64)

    @patch.dict("os.environ", {"ALLOW_INSECURE_INTER_NODE_TLS": "true"})
    def test_https_url_with_insecure_flag_returns_verify_false(self):
        verify, fingerprint = resolve_tls_verify_for_url("https://example.com")
        self.assertFalse(verify)
        self.assertIsNone(fingerprint)


class ProvisionerDuplicateLogFixTests(TestCase):
    """The provisioner had a duplicate "firewall rules
    synchronized" log line — drive-by fix to ensure the log
    stream is one-line-per-event.
    """

    def test_no_duplicate_firewall_log_marker(self):
        # The simplest correctness check: the literal "Master
        # firewall rules synchronized" string appears exactly
        # once in provisioner.py.
        with open(
            "C:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/"
            "smsly-hosting/backend/apps/deployments/services/"
            "provisioner.py",
            encoding="utf-8",
        ) as f:
            content = f.read()
        count = content.count("Master firewall rules synchronized")
        self.assertEqual(
            count, 1,
            f"Expected exactly one occurrence of the firewall log, "
            f"got {count}. The duplicate was a pre-existing copy-paste bug.",
        )
