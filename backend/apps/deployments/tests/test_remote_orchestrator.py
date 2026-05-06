import json
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Deployment, ManagedServer, PlatformConfig, Service
from apps.deployments.services.remote_orchestrator import RemoteOrchestrator


class RemoteOrchestratorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="remote", password="pass")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="worker",
            host="203.0.113.10",
            api_url="https://worker.example.com",
            api_token="smsly_stale_token",
            gateway_secret="shared-secret",
        )
        self.service = Service.objects.create(
            owner=self.user,
            name="api",
            repository_url="https://github.com/example/api.git",
        )

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_falls_back_to_hmac_when_token_is_rejected(self, request_mock):
        token_response = Mock(status_code=403, text="forbidden")
        hmac_response = Mock(status_code=200)
        hmac_response.json.return_value = {
            "results": [{"id": "remote-service-id", "name": "api"}]
        }
        request_mock.side_effect = [token_response, hmac_response]

        remote_id = RemoteOrchestrator(self.server).sync_service(self.service)

        self.assertEqual(remote_id, "remote-service-id")
        self.assertEqual(request_mock.call_count, 2)
        first_headers = request_mock.call_args_list[0].kwargs["headers"]
        second_headers = request_mock.call_args_list[1].kwargs["headers"]
        self.assertEqual(first_headers["Authorization"], "Bearer smsly_stale_token")
        self.assertIn("X-Gateway-Signature-V2", second_headers)
        self.assertEqual(second_headers["X-SMSLY-Remote-Sync"], "1")
        self.assertIn("/api/v1/services/?search=api", request_mock.call_args_list[1].args[1])

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_trigger_deploy_posts_ref_payload_expected_by_remote_api(self, request_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = "198.51.100.20"
        cfg.save(update_fields=["server_ip"])
        deployment = Deployment.objects.create(
            service=self.service,
            commit_hash="abc123",
            commit_message="deploy abc123",
        )
        response = Mock(status_code=202)
        response.json.return_value = {"id": "remote-deployment-id"}
        request_mock.return_value = response

        remote_id = RemoteOrchestrator(self.server).trigger_deploy(
            deployment,
            "remote-service-id",
        )

        self.assertEqual(remote_id, "remote-deployment-id")
        payload = json.loads(request_mock.call_args.kwargs["data"].decode())
        self.assertEqual(payload["ref"], "abc123")
        self.assertEqual(payload["source_node"], "198.51.100.20")
        self.assertNotIn("commit_hash", payload)

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_approve_deployment_posts_to_remote_approval_endpoint(self, request_mock):
        response = Mock(status_code=200)
        request_mock.return_value = response

        ok = RemoteOrchestrator(self.server).approve_deployment(
            "remote-deployment-id",
            payload={"memory_mb": 512},
        )

        self.assertTrue(ok)
        self.assertIn(
            "/api/v1/deployments/remote-deployment-id/approve/",
            request_mock.call_args.args[1],
        )
        payload = json.loads(request_mock.call_args.kwargs["data"].decode())
        self.assertEqual(payload["memory_mb"], 512)

    @patch("apps.deployments.services.remote_orchestrator.time.sleep", return_value=None)
    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_preserves_remote_connection_error(self, request_mock, _sleep):
        request_mock.side_effect = requests.exceptions.SSLError(
            "tlsv1 alert internal error"
        )

        orchestrator = RemoteOrchestrator(self.server)
        remote_id = orchestrator.sync_service(self.service)

        self.assertIsNone(remote_id)
        self.assertGreaterEqual(request_mock.call_count, 1)
        self.assertIn("tlsv1 alert internal error", orchestrator.describe_last_error())

    @patch("apps.deployments.services.remote_orchestrator.requests.request")
    def test_sync_service_does_not_create_when_search_redirects(self, request_mock):
        redirect = Mock(status_code=308, text="")
        redirect.headers = {"Location": "https://203.0.113.10/api/v1/services/"}
        request_mock.return_value = redirect

        orchestrator = RemoteOrchestrator(self.server)
        remote_id = orchestrator.sync_service(self.service)

        self.assertIsNone(remote_id)
        self.assertIn("redirected", orchestrator.describe_last_error())
        self.assertTrue(
            all(call.args[0] == "GET" for call in request_mock.call_args_list)
        )
