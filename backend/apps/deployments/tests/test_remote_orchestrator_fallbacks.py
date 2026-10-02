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

    @patch("apps.deployments.services.remote_orchestrator.client.requests.request")
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

    @patch("apps.deployments.services.remote_orchestrator.client.requests.request")
    def test_token_401_falls_through_to_hmac_without_exchange(self, mock_request):
        """A token 401 must try hmac next, not re-exchange in a loop.

        Regression 2026-10-02: HMAC-only endpoints (transfer sync,
        agent APIs) reject tokens structurally. Re-exchanging on 401
        recursed forever — minting a fresh token each round — while
        hmac never ran, so register-incoming never succeeded.
        """
        self.server.api_token = "stale-token"
        self.server.save(update_fields=["api_token"])
        orch = RemoteOrchestrator(self.server)

        denied = MagicMock()
        denied.status_code = 401
        denied.json.return_value = {"error": "Valid node authentication is required."}
        accepted = MagicMock()
        accepted.status_code = 200
        accepted.json.return_value = {"id": "target-1"}
        mock_request.side_effect = [denied, accepted]

        with patch.object(
            RemoteOrchestrator, "_try_gateway_token_exchange",
            side_effect=AssertionError("must not re-exchange while hmac is untried"),
        ):
            with patch.object(
                RemoteOrchestrator, "_candidate_base_urls",
                return_value=["http://10.100.0.2:8000"],
            ):
                resp = orch._request("POST", "/api/v1/transfers/register-incoming/")

        self.assertIsNotNone(resp)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_request.call_count, 2)
        hmac_headers = mock_request.call_args[1].get("headers", {})
        self.assertIn("X-Gateway-Signature-V2", hmac_headers)
        self.assertNotIn("Authorization", hmac_headers)
