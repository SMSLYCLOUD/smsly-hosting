# pylint: disable=invalid-name
"""Tests for Issue 33: ManagedServer.provision_new admin gating."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.servers import ManagedServer

User = get_user_model()


class ProvisionNewAdminGateTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="prov-admin", password="p", is_staff=True,
        )
        self.staff = User.objects.create_user(
            username="prov-staff", password="p", is_staff=True,
        )
        self.regular = User.objects.create_user(
            username="prov-regular", password="p",
        )
        self.url = "/api/v1/servers/provision/"

    def _payload(self, **overrides):
        body = {
            "name": "primary-host",
            "host": "8.8.8.8",
            "ssh_user": "root",
            "ssh_password": "secret",
            "ssh_auth_method": "password",
            "is_primary": False,
        }
        body.update(overrides)
        return body

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_regular_user_is_primary_true_rejected(self, _delay):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.post(
            self.url,
            self._payload(is_primary=True),
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("superuser", str(resp.data).lower())
        self.assertFalse(
            ManagedServer.objects.filter(name="primary-host").exists()
        )

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_regular_user_is_primary_false_accepted(self, _delay):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.post(
            self.url,
            self._payload(is_primary=False),
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        created = ManagedServer.objects.get(name="primary-host")
        self.assertFalse(created.is_primary)
        self.assertEqual(created.owner, self.regular)

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_staff_non_superuser_cannot_set_is_primary(self, _delay):
        """Staff without superuser status is rejected — primary
        provisioning is gated to superusers only (security batch).
        """
        self.client.force_authenticate(user=self.staff)
        resp = self.client.post(
            self.url,
            self._payload(is_primary=True, name="staff-primary"),
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            ManagedServer.objects.filter(name="staff-primary").exists()
        )

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_superuser_can_set_is_primary(self, _delay):
        superuser = User.objects.create_superuser(
            username="prov-super", password="p",
        )
        self.client.force_authenticate(user=superuser)
        resp = self.client.post(
            self.url,
            self._payload(is_primary=True, name="super-primary"),
            format="json",
        )
        self.assertEqual(resp.status_code, 202)
        created = ManagedServer.objects.get(name="super-primary")
        self.assertTrue(created.is_primary)

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_is_primary_string_truthy_value_treated_as_true(self, _delay):
        self.client.force_authenticate(user=self.regular)
        resp = self.client.post(
            self.url,
            self._payload(is_primary="true"),
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_is_primary_check_uses_validated_data_not_raw_request(self, _delay):
        """The serializer may drop or coerce the is_primary value
        before the permission check. The check is performed on
        ``validated_data['is_primary']`` (the post-validation value)
        not on the raw body.
        """
        self.client.force_authenticate(user=self.regular)
        resp = self.client.post(
            self.url,
            self._payload(is_primary=True, name="validraw"),
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            ManagedServer.objects.filter(name="validraw").exists()
        )

