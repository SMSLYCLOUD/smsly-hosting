from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer, Service
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorErrors5(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_orch_err_5", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="error-server-5",
            host="203.0.113.19",
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
    def test_sync_service_fails_when_search_fails(self, mock_search):
        orch = RemoteOrchestrator(self.server)

        mock_search.side_effect = Exception("Connection refused")

        result = orch.sync_service(self.service)
        self.assertIsNone(result)
        self.assertIn("Connection refused", orch.last_error)
        self.assertIn("Failed to search service", orch.last_error)
