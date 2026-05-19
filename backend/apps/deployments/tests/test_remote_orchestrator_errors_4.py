import pytest
from django.test import TestCase
import os


from unittest.mock import patch, MagicMock
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.models import ManagedServer
from django.contrib.auth import get_user_model

@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorErrors4(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_orch_err_4", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="error-server-4",
            host="203.0.113.18",
            api_url="https://test.example.com",
            api_token="token",
            gateway_secret="secret",
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_reports_auth_missing(self, mock_request):
        self.server.api_token = ""
        self.server.gateway_secret = ""
        self.server.save()

        orch = RemoteOrchestrator(self.server)

        result = orch._request("GET", "/api/v1/test/")
        self.assertIsNone(result)
        self.assertIn("Remote API credentials are missing", orch.last_error)
        self.assertFalse(mock_request.called)
