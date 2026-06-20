from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorErrors(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_orch_err", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="error-server",
            host="203.0.113.15",
            api_url="https://test.example.com",
            api_token="token",
            gateway_secret="secret",
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_preserves_error_details(self, mock_request):
        orch = RemoteOrchestrator(self.server)

        response_mock = MagicMock()
        response_mock.status_code = 500
        response_mock.json.side_effect = ValueError("Invalid JSON")
        response_mock.text = "Internal Server Error"
        mock_request.return_value = response_mock

        orch._request("GET", "/api/v1/test/")
        self.assertIn("Internal Server Error", orch.last_error)
