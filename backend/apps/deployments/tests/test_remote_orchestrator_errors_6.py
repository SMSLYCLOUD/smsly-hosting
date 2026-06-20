from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer, Service
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorErrors6(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_orch_err_6", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="error-server-6",
            host="203.0.113.20",
            api_url="https://test.example.com",
            api_token="token",
            gateway_secret="secret",
        )
        self.service = Service.objects.create(
            owner=self.user,
            name="api",
            repository_url="https://github.com/example/api.git",
        )

    def tearDown(self):
        self.service.delete()
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.remote_orchestrator.RemoteOrchestrator._search_remote_service")
    @patch("apps.deployments.services.remote_orchestrator.RemoteOrchestrator._request")
    def test_sync_service_fails_when_create_fails(self, mock_request, mock_search):
        orch = RemoteOrchestrator(self.server)

        mock_search.return_value = ""
        mock_request.return_value = MagicMock(status_code=500, text="Internal Server Error")

        result = orch.sync_service(self.service)
        self.assertIsNone(result)
        self.assertIn("Failed to create service", orch.last_error)
