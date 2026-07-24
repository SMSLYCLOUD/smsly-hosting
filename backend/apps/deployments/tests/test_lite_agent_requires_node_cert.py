# pylint: disable=invalid-name
"""Tests for ``is_lite_agent=True`` requiring ``node_certificate`` (Issue 143).

A user could previously mark a server as ``is_lite_agent=True``
and bypass many of the normal ``ManagedServer`` checks.  The
fix requires ``node_certificate`` to be present and non-empty
when ``is_lite_agent=True`` in both
``ManagedServerCreateSerializer`` and
``ManagedServerProvisionSerializer``.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.views.server import (
    ManagedServerCreateSerializer,
    ManagedServerProvisionSerializer,
)

User = get_user_model()


class IsLiteAgentRequiresNodeCertificateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="lite-cert-user", password="x",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_serializer_rejects_lite_agent_without_cert(self):
        ser = ManagedServerCreateSerializer(data={
            "name": "Edge",
            "host": "198.51.100.5",
            "is_lite_agent": True,
        })
        self.assertFalse(ser.is_valid())
        self.assertIn("node_certificate", ser.errors)

    def test_create_serializer_rejects_empty_cert(self):
        ser = ManagedServerCreateSerializer(data={
            "name": "Edge",
            "host": "198.51.100.5",
            "is_lite_agent": True,
            "node_certificate": "   ",
        })
        self.assertFalse(ser.is_valid())
        self.assertIn("node_certificate", ser.errors)

    def test_create_serializer_accepts_lite_agent_with_cert(self):
        ser = ManagedServerCreateSerializer(data={
            "name": "Edge",
            "host": "198.51.100.5",
            "is_lite_agent": True,
            "node_certificate": "PEM-CONTENT",
        })
        self.assertTrue(
            ser.is_valid(),
            f"Unexpected errors: {ser.errors}",
        )

    def test_create_serializer_allows_non_lite_without_cert(self):
        ser = ManagedServerCreateSerializer(data={
            "name": "Edge",
            "host": "198.51.100.5",
            "is_lite_agent": False,
        })
        self.assertTrue(ser.is_valid(), f"Errors: {ser.errors}")

    def test_provision_serializer_accepts_lite_agent_without_cert(self):
        ser = ManagedServerProvisionSerializer(data={
            "name": "EdgeProv",
            "host": "198.51.100.5",
            "ssh_auth_method": "password",
            "ssh_password": "secret",
            "is_lite_agent": True,
        })
        self.assertTrue(ser.is_valid(), f"Errors: {ser.errors}")

    def test_provision_serializer_accepts_lite_agent_with_cert(self):
        ser = ManagedServerProvisionSerializer(data={
            "name": "EdgeProv",
            "host": "198.51.100.5",
            "ssh_auth_method": "password",
            "ssh_password": "secret",
            "is_lite_agent": True,
            "node_certificate": "PEM-CONTENT",
        })
        self.assertTrue(
            ser.is_valid(),
            f"Unexpected errors: {ser.errors}",
        )

    def test_create_endpoint_returns_400_for_lite_without_cert(self):
        resp = self.client.post(
            "/api/v1/servers/",
            {
                "name": "Edge",
                "host": "198.51.100.5",
                "is_lite_agent": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("node_certificate", str(resp.data))
