from unittest.mock import patch

import pytest
import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorErrors2(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_orch_err_2", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="error-server-2",
            host="203.0.113.16",
            api_url="https://test.example.com",
            api_token="token",
            gateway_secret="secret",
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_preserves_network_error(self, mock_request):
        orch = RemoteOrchestrator(self.server)

        mock_request.side_effect = requests.RequestException("Connection refused")

        orch._request("GET", "/api/v1/test/")
        self.assertIn("Connection refused", orch.last_error)
