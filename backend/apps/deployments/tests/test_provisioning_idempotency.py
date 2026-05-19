import pytest
from django.test import TestCase
import os


from unittest.mock import patch, MagicMock
from apps.deployments.services.provisioner import provision_server
from apps.deployments.models import ManagedServer
from django.contrib.auth import get_user_model

@pytest.mark.django_db(transaction=True)
class TestProvisioningIdempotency(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="prov_user", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="idempotent-worker",
            host="203.0.113.11",
            api_url="",
            api_token="",
            provision_status=ManagedServer.ProvisionStatus.PROVISIONING,
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.provisioner._get_ssh_client")
    def test_duplicate_provisioning_fails_fast(self, mock_ssh):
        server2 = ManagedServer.objects.create(
            owner=self.user,
            name="duplicate",
            host="203.0.113.11",
            api_url="",
            api_token="",
            provision_status=ManagedServer.ProvisionStatus.PENDING,
        )

        # Provision the second one
        provision_server(str(server2.id))

        server2.refresh_from_db()
        self.assertEqual(server2.provision_status, ManagedServer.ProvisionStatus.FAILED)
        self.assertIn("already running for this host", server2.provision_logs)

        server2.delete()
