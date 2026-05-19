import pytest
from django.test import TestCase
import os


from unittest.mock import patch, MagicMock
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
from apps.deployments.models import ManagedServer
from django.contrib.auth import get_user_model

@pytest.mark.django_db(transaction=True)
class TestRemoteOrchestratorVerify(TestCase):
    def test_verify_ssl_enforcement(self):
        server = MagicMock(spec=ManagedServer)
        server.api_url = "https://example.com"
        server.api_token = "token"

        orchestrator = RemoteOrchestrator(server)

        with patch('apps.deployments.services.remote_orchestrator._REMOTE_VERIFY', True):
            with patch("requests.request") as mock_request:
                mock_request.return_value.status_code = 200
                mock_request.return_value.json.return_value = {"id": "123"}
                orchestrator._request("GET", "/api/v1/test/")
                self.assertTrue(mock_request.called)
                _, kwargs = mock_request.call_args
                self.assertTrue(kwargs.get("verify") is True)

        with patch('apps.deployments.services.remote_orchestrator._REMOTE_VERIFY', False):
            with patch("requests.request") as mock_request:
                mock_request.return_value.status_code = 200
                mock_request.return_value.json.return_value = {"id": "123"}
                orchestrator._request("GET", "/api/v1/test/")
                self.assertTrue(mock_request.called)
                _, kwargs = mock_request.call_args
                self.assertTrue(kwargs.get("verify") is False)
