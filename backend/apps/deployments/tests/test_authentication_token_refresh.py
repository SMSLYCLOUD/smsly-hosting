import pytest
from django.test import TestCase
import os


from unittest.mock import patch, MagicMock
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.models import ManagedServer
from django.contrib.auth import get_user_model
import requests

@pytest.mark.django_db(transaction=True)
class TestAuthTokenRefresh(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="test_auth_1", password="123")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="test-server-1",
            host="203.0.113.13",
            api_url="https://test.example.com",
            api_token="smsly_expired_token",
            gateway_secret="my-gateway-secret",
        )

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_refreshes_expired_token(self, mock_request):
        orch = RemoteOrchestrator(self.server)

        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.json.return_value = {"token": "smsly_new_valid_token"}
        mock_request.return_value = response_mock

        success = orch._exchange_gateway_secret_for_token("https://test.example.com")
        self.assertTrue(success)
        self.server.refresh_from_db()
        self.assertEqual(self.server.api_token, "smsly_new_valid_token")

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_fails_when_hmac_rejected(self, mock_request):
        orch = RemoteOrchestrator(self.server)

        response_mock = MagicMock()
        response_mock.status_code = 401
        mock_request.return_value = response_mock

        success = orch._exchange_gateway_secret_for_token("https://test.example.com")
        self.assertFalse(success)
        self.server.refresh_from_db()
        self.assertEqual(self.server.api_token, "smsly_expired_token")
