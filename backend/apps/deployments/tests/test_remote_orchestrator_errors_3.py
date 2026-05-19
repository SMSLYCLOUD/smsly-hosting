import pytest
from django.test import TestCase
import os


from unittest.mock import patch, MagicMock
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.models import ManagedServer
from django.contrib.auth import get_user_model
import requests

@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorErrors3(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_orch_err_3", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="error-server-3",
            host="203.0.113.17",
            api_url="https://test.example.com",
            api_token="token",
            gateway_secret="secret",
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_redirects(self, mock_request):
        orch = RemoteOrchestrator(self.server)

        response_mock = MagicMock()
        response_mock.status_code = 301
        response_mock.headers = {"Location": "https://other.example.com"}
        mock_request.return_value = response_mock

        orch._request("GET", "/api/v1/test/")
        self.assertIn("redirected", orch.last_error)
        self.assertIn("https://other.example.com", orch.last_error)
