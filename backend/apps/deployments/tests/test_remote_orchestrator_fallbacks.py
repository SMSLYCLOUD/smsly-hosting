from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import ManagedServer
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorFallbacks(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_fallback", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="fallback-server",
            host="203.0.113.14",
            api_url="https://test.example.com",
            api_token="",
            gateway_secret="my-gateway-secret",
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_request_tries_hmac_when_token_missing(self, mock_request):
        orch = RemoteOrchestrator(self.server)

        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.json.return_value = {"id": "test"}
        mock_request.return_value = response_mock

        orch._request("GET", "/api/v1/test/")
        self.assertTrue(mock_request.called)

        headers = mock_request.call_args[1].get("headers", {})
        self.assertEqual(headers.get("X-SMSLY-Remote-Sync"), "1")
        self.assertIn("X-Gateway-Signature-V2", headers)
