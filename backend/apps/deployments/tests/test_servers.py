# pylint: disable=invalid-name
"""Tests for ManagedServer model and views."""

import hashlib
import hmac
import json
import os
import time
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider

from ..models import Deployment, Service  # type: ignore[attr-defined]
from ..models.servers import ManagedServer
from ..tasks import update_remote_server_task
from ..tasks.deploy.build import fleet_build_lock
from ..tasks.deploy.queue import (
    enqueue_smart_deploy_task,
    recover_stalled_queued_deployments,
    should_skip_review_for_commit_message,
)
from ..views.server.helpers import _build_remote_headers

User = get_user_model()


class ManagedServerModelTests(TestCase):
    """Test the ManagedServer model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="srvuser", email="srv@test.com", password="testpass123"
        )

    def test_create_server(self):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Prod VPS",
            host="198.51.100.5",
            api_url="https://prod.example.com",
            api_token="smsly_test_token_123",
            ssh_port=22,
        )
        self.assertEqual(str(server), "Prod VPS (198.51.100.5)")
        self.assertEqual(server.status, ManagedServer.Status.UNKNOWN)

    def test_primary_flag(self):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Primary",
            host="10.0.0.1",
            api_url="https://primary.example.com",
            api_token="tok",
            is_primary=True,
        )
        self.assertTrue(server.is_primary)

    def test_build_remote_headers_uses_correct_token_scheme(self):
        bearer_server = ManagedServer.objects.create(
            owner=self.user,
            name="BearerSrv",
            host="10.0.0.10",
            api_url="https://bearer.example.com",
            api_token="smsly_token_123",
        )
        token_server = ManagedServer.objects.create(
            owner=self.user,
            name="TokenSrv",
            host="10.0.0.11",
            api_url="https://token.example.com",
            api_token="drf_token_abc",
        )
        prefixed_server = ManagedServer.objects.create(
            owner=self.user,
            name="PrefixedSrv",
            host="10.0.0.12",
            api_url="https://prefixed.example.com",
            api_token="Token already_prefixed",
        )

        self.assertEqual(
            _build_remote_headers(bearer_server)["Authorization"],
            "Bearer smsly_token_123",
        )
        self.assertEqual(
            _build_remote_headers(token_server)["Authorization"],
            "Token drf_token_abc",
        )
        self.assertEqual(
            _build_remote_headers(prefixed_server)["Authorization"],
            "Token already_prefixed",
        )


class NodeTokenExchangeTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="testpass123",
        )

    @override_settings(GATEWAY_SECRET="node-secret")
    def test_hmac_exchange_uses_raw_body_before_request_data(self):
        url = reverse("node-token-exchange-hmac")
        body = json.dumps(
            {"node_name": "Primary"},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        timestamp = str(int(time.time()))
        nonce = "node-exchange-test-nonce"
        body_hash = hashlib.sha256(body).hexdigest()
        # SECURITY (Batch G): nonce is mandatory and bound into the
        # signed payload.
        payload = f"POST|{url}|{timestamp}|{nonce}|{body_hash}"
        signature = hmac.new(
            b"node-secret",
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        response = self.client.generic(
            "POST",
            url,
            body,
            content_type="application/json",
            HTTP_X_GATEWAY_SIGNATURE_V2=signature,
            HTTP_X_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_REQUEST_NONCE=nonce,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["token"].startswith("smsly_"))


class ManagedServerViewTests(TestCase):
    """Test the ManagedServer API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="srvview", email="srvview@test.com", password="testpass123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_servers_empty(self):
        resp = self.client.get("/api/v1/servers/")
        self.assertEqual(resp.status_code, 200)

    def test_create_server(self):
        resp = self.client.post("/api/v1/servers/", {
            "name": "Test Server",
            "host": "10.0.0.1",
            "api_url": "https://test.example.com",
            "api_token": "smsly_testtoken",
            "ssh_port": 22,
        })
        self.assertEqual(resp.status_code, 201)
        self.assertIn("id", resp.data)
        self.assertIn("status", resp.data)
        self.assertNotIn("api_token", resp.data)
        self.assertNotIn("gateway_secret", resp.data)

    def test_delete_server(self):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Del",
            host="10.0.0.2",
            api_url="https://del.example.com",
            api_token="tok",
        )
        resp = self.client.delete(f"/api/v1/servers/{server.id}/")
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(ManagedServer.objects.count(), 0)

    def test_other_users_server_not_visible(self):
        other = User.objects.create_user(
            username="other", email="other@test.com", password="pass"
        )
        ManagedServer.objects.create(
            owner=other,
            name="Hidden",
            host="10.0.0.3",
            api_url="https://hidden.example.com",
            api_token="tok",
        )
        resp = self.client.get("/api/v1/servers/")
        data = resp.data.get("results", resp.data)
        servers = data if isinstance(data, list) else []
        self.assertEqual(len(servers), 0)

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_health_check_online(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "1", "name": "svc1"}]
        mock_get.return_value = mock_resp

        server = ManagedServer.objects.create(
            owner=self.user,
            name="HC",
            host="10.0.0.4",
            api_url="https://hc.example.com",
            api_token="tok",
        )
        resp = self.client.post(f"/api/v1/servers/{server.id}/health_check/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ONLINE")

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_health_check_auto_detects_blank_api_url(self, mock_get):
        def fake_get(url, *args, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if url.endswith("/health"):
                mock_resp.json.return_value = {"status": "healthy", "version": "2.0.0"}
            else:
                mock_resp.json.return_value = {"results": [{"id": "1"}, {"id": "2"}]}
            return mock_resp

        mock_get.side_effect = fake_get

        server = ManagedServer.objects.create(
            owner=self.user,
            name="Auto URL",
            host="10.0.0.6",
            api_url="",
            api_token="tok",
        )

        resp = self.client.post(f"/api/v1/servers/{server.id}/health_check/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ONLINE")
        self.assertIn(resp.data["api_url"], ["http://10.0.0.6", "http://10.0.0.6:8090"])
        self.assertEqual(resp.data["server_version"], "2.0.0")
        self.assertEqual(resp.data["services_count"], 2)

        server.refresh_from_db()
        self.assertIn(server.api_url, ["http://10.0.0.6", "http://10.0.0.6:8090"])

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_health_check_repairs_stale_https_api_url(self, mock_get):
        import requests as req

        def fake_get(url, *args, **kwargs):
            if url.startswith("https://"):
                raise req.ConnectionError("443 closed")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if url.endswith("/health"):
                mock_resp.json.return_value = {"status": "healthy"}
            else:
                mock_resp.json.return_value = []
            return mock_resp

        mock_get.side_effect = fake_get

        server = ManagedServer.objects.create(
            owner=self.user,
            name="Stale URL",
            host="10.0.0.7",
            api_url="https://10.0.0.7",
            api_token="tok",
        )

        resp = self.client.post(f"/api/v1/servers/{server.id}/health_check/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "ONLINE")
        self.assertIn(resp.data["api_url"], ["http://10.0.0.7", "http://10.0.0.7:8090"])

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_health_check_offline(self, mock_get):
        import requests as req
        mock_get.side_effect = req.ConnectionError("Connection refused")

        server = ManagedServer.objects.create(
            owner=self.user,
            name="Down",
            host="10.0.0.5",
            api_url="https://down.example.com",
            api_token="tok",
        )
        resp = self.client.post(f"/api/v1/servers/{server.id}/health_check/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "OFFLINE")

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_domains_aggregation(self, mock_get):
        """Test that the domains endpoint aggregates custom_domains across services."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {
                "id": "svc1",
                "name": "Frontend",
                "public_domain": "frontend.cloud.smsly.cloud",
                "custom_domains": ["app.example.com", "www.example.com"],
                "domain_verified": True,
                "verification_token": "abc123",
            },
            {
                "id": "svc2",
                "name": "API",
                "public_domain": "api.cloud.smsly.cloud",
                "custom_domains": ["api.example.com"],
                "domain_verified": False,
                "verification_token": "def456",
            },
            {
                "id": "svc3",
                "name": "Worker",
                "public_domain": "",
                "custom_domains": [],
            },
        ]
        mock_get.return_value = mock_resp

        server = ManagedServer.objects.create(
            owner=self.user,
            name="DomTest",
            host="10.0.0.6",
            api_url="https://domtest.example.com",
            api_token="tok",
        )
        resp = self.client.get(f"/api/v1/servers/{server.id}/domains/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 3)
        domains = resp.data["domains"]
        self.assertEqual(domains[0]["domain"], "app.example.com")
        self.assertEqual(domains[0]["service_name"], "Frontend")
        self.assertEqual(domains[0]["verified"], True)
        self.assertEqual(domains[1]["domain"], "www.example.com")
        self.assertEqual(domains[2]["domain"], "api.example.com")
        self.assertEqual(domains[2]["verified"], False)

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_remote_services_non_json_payload_returns_safe_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("not json")
        mock_get.return_value = mock_resp

        server = ManagedServer.objects.create(
            owner=self.user,
            name="SrvNonJson",
            host="10.0.0.9",
            api_url="https://srv-nonjson.example.com",
            api_token="tok",
        )

        resp = self.client.get(f"/api/v1/servers/{server.id}/services/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["remote_unreachable"])
        self.assertEqual(resp.data["kind"], "services")
        self.assertEqual(resp.data["results"], [])

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_remote_services_upstream_error_returns_safe_response(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_get.return_value = mock_resp

        server = ManagedServer.objects.create(
            owner=self.user,
            name="Srv502",
            host="10.0.0.11",
            api_url="https://srv-502.example.com",
            api_token="tok",
        )

        resp = self.client.get(f"/api/v1/servers/{server.id}/services/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["remote_unreachable"])
        self.assertEqual(resp.data["kind"], "services")
        self.assertEqual(resp.data["upstream_status"], 502)

    def test_lite_agent_proxy_services_uses_local_shared_db(self):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Lite Agent",
            host="10.0.0.12",
            is_lite_agent=True,
        )
        service = Service.objects.create(
            owner=self.user,
            name="transferred-lite-service",
            server=server,
        )

        resp = self.client.post(
            f"/api/v1/servers/{server.id}/proxy/",
            {
                "method": "GET",
                "path": "/api/v1/services/",
            },
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status_code"], 200)
        self.assertEqual(resp.data["data"]["count"], 1)
        self.assertEqual(resp.data["data"]["results"][0]["id"], str(service.id))

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_proxy_request_exception_returns_proxy_envelope_not_502(self, mock_request):
        mock_request.side_effect = requests.RequestException("connection refused")
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Remote Down",
            host="10.0.0.13",
            api_url="https://10.0.0.13",
            api_token="tok",
        )

        resp = self.client.post(
            f"/api/v1/servers/{server.id}/proxy/",
            {
                "method": "GET",
                "path": "/api/v1/services/",
            },
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status_code"], 502)
        self.assertTrue(resp.data["data"]["remote_unreachable"])

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_remote_services_falls_back_to_gateway_secret_when_token_fails(self, mock_get):
        first = MagicMock()
        first.status_code = 403

        second = MagicMock()
        second.status_code = 200
        second.json.return_value = {"results": [{"id": "svc1"}], "count": 1}

        mock_get.side_effect = [first, second]

        server = ManagedServer.objects.create(
            owner=self.user,
            name="SrvFallbackAuth",
            host="10.0.0.12",
            api_url="https://srv-fallback.example.com",
            api_token="tok",
            gateway_secret="gw-secret",
        )

        resp = self.client.get(f"/api/v1/servers/{server.id}/services/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 1)
        self.assertEqual(mock_get.call_count, 2)

        first_headers = mock_get.call_args_list[0].kwargs["headers"]
        second_headers = mock_get.call_args_list[1].kwargs["headers"]
        self.assertIn("Authorization", first_headers)
        self.assertIn("X-Gateway-Signature-V2", second_headers)

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_domains_handles_null_custom_domains(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "id": "svc1",
                "name": "Frontend",
                "public_domain": "frontend.cloud.smsly.cloud",
                "custom_domains": None,
            }
        ]
        mock_get.return_value = mock_resp

        server = ManagedServer.objects.create(
            owner=self.user,
            name="SrvNullDomains",
            host="10.0.0.10",
            api_url="https://srv-nulldomains.example.com",
            api_token="tok",
        )

        resp = self.client.get(f"/api/v1/servers/{server.id}/domains/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 0)
        self.assertEqual(resp.data["domains"], [])

    @patch("apps.deployments.views.server.helpers.requests.get")
    def test_domains_aggregation_uses_all_paginated_service_pages(self, mock_get):
        first = MagicMock()
        first.status_code = 200
        first.json.return_value = {
            "results": [
                {
                    "id": "svc1",
                    "name": "Frontend",
                    "public_domain": "frontend.cloud.smsly.cloud",
                    "custom_domains": ["app.example.com"],
                    "domain_verified": True,
                    "verification_token": "abc123",
                }
            ],
            "next": "https://remote.example.com/api/v1/services/?page=2",
        }

        second = MagicMock()
        second.status_code = 200
        second.json.return_value = {
            "results": [
                {
                    "id": "svc2",
                    "name": "API",
                    "public_domain": "api.cloud.smsly.cloud",
                    "custom_domains": ["api.example.com"],
                    "domain_verified": False,
                    "verification_token": "def456",
                }
            ],
            "next": None,
        }

        mock_get.side_effect = [first, second]

        server = ManagedServer.objects.create(
            owner=self.user,
            name="SrvPagedDomains",
            host="10.0.0.13",
            api_url="https://srv-paged.example.com",
            api_token="tok",
        )

        resp = self.client.get(f"/api/v1/servers/{server.id}/domains/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["count"], 2)
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(resp.data["domains"][0]["domain"], "app.example.com")
        self.assertEqual(resp.data["domains"][1]["domain"], "api.example.com")

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_update_server_queues_task_when_ssh_credentials_exist(self, delay_mock):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="UpdateMe",
            host="10.0.0.20",
            ssh_user="root",
            ssh_password="secret",
        )

        resp = self.client.post(f"/api/v1/servers/{server.id}/update-server/")

        self.assertEqual(resp.status_code, 202)
        delay_mock.assert_called_once()
        args, kwargs = delay_mock.call_args
        self.assertEqual(args, (str(server.id),))
        self.assertEqual(kwargs, {"skip_reboot": True})
        server.refresh_from_db()
        self.assertIn("Update started by", server.provision_logs)

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_update_server_rejects_missing_ssh_credentials(self, delay_mock):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="NoSSH",
            host="10.0.0.21",
        )

        resp = self.client.post(f"/api/v1/servers/{server.id}/update-server/")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("SSH credentials", resp.data["error"])
        delay_mock.assert_not_called()

    @patch("apps.deployments.services.provisioner.provision_server.delay")
    def test_update_server_auto_clears_stalled_status(self, delay_mock):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Busy",
            host="10.0.0.22",
            ssh_password="secret",
            provision_status=ManagedServer.ProvisionStatus.UPDATING,
        )

        resp = self.client.post(f"/api/v1/servers/{server.id}/update-server/")

        self.assertEqual(resp.status_code, 202)
        server.refresh_from_db()
        self.assertNotEqual(server.provision_status, ManagedServer.ProvisionStatus.UPDATING)
        delay_mock.assert_called_once()

    def test_update_server_name(self):
        """Test that a server name can be updated via PATCH."""
        server = ManagedServer.objects.create(
            owner=self.user,
            name="OldName",
            host="10.0.0.7",
            api_url="https://old.example.com",
            api_token="tok",
        )
        resp = self.client.patch(
            f"/api/v1/servers/{server.id}/",
            {"name": "NewName"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], str(server.id))
        self.assertIn("status", resp.data)
        self.assertNotIn("api_token", resp.data)
        self.assertNotIn("gateway_secret", resp.data)
        server.refresh_from_db()
        self.assertEqual(server.name, "NewName")


class RemoteServerUpdateTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="srvupdate", email="srvupdate@test.com", password="testpass123"
        )
        cache.clear()

    @patch("apps.deployments.services.ssh_client.SSHClient")
    def test_update_task_runs_preflight_installer_and_postflight(self, ssh_cls):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Worker",
            host="10.0.0.30",
            ssh_password="secret",
        )
        ssh = ssh_cls.return_value
        ssh.find_hosting_path.return_value = "/opt/smsly-hosting"
        ssh.exec_command.side_effect = [
            ("preflight ok\n", "", 0),
            ("installer ok\n", "", 0),
            ("postflight ok\n", "", 0),
        ]

        ok = update_remote_server_task.run(str(server.id))

        self.assertTrue(ok)
        server.refresh_from_db()
        self.assertEqual(server.provision_status, ManagedServer.ProvisionStatus.DONE)
        self.assertIn("preflight ok", server.provision_logs)
        self.assertIn("installer ok", server.provision_logs)
        self.assertIn("postflight ok", server.provision_logs)
        self.assertEqual(ssh.exec_command.call_count, 3)
        ssh.close.assert_called_once()

    @patch("apps.deployments.services.ssh_client.SSHClient")
    def test_update_task_marks_failed_when_preflight_fails(self, ssh_cls):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Broken",
            host="10.0.0.31",
            ssh_password="secret",
        )
        ssh = ssh_cls.return_value
        ssh.find_hosting_path.return_value = "/opt/smsly-hosting"
        ssh.exec_command.return_value = ("", "docker daemon is not reachable\n", 16)

        ok = update_remote_server_task.run(str(server.id))

        self.assertFalse(ok)
        server.refresh_from_db()
        self.assertEqual(server.provision_status, ManagedServer.ProvisionStatus.FAILED)
        self.assertIn("docker daemon is not reachable", server.provision_logs)
        self.assertIn("preflight failed", server.provision_logs.lower())

    @patch.dict(
        os.environ,
        {
            "DATABASE_URL": "postgresql://smsly_admin:master-db@db:5432/smsly_hosting",
            "RABBITMQ_PASSWORD": "master-mq",
            "REDIS_PASSWORD": "master-redis",
            "GATEWAY_SECRET": "master-gateway",
        },
        clear=False,
    )
    @patch("apps.deployments.services.provisioner._provision_node_db_credentials")
    @patch("apps.deployments.services.ssh_client.SSHClient")
    def test_lite_update_preserves_agent_mode_and_node_queue(
        self,
        ssh_cls,
        provision_db_mock,
    ):
        provision_db_mock.return_value = ("node_agent_abcd", "node-db-pass")
        server = ManagedServer.objects.create(
            owner=self.user,
            name="Lite Worker",
            host="10.0.0.32",
            ssh_password="secret",
            is_lite_agent=True,
            gateway_secret="master-gateway",
        )
        ssh = ssh_cls.return_value
        ssh.find_hosting_path.return_value = "/opt/smsly-hosting"
        ssh.exec_command.side_effect = [
            ("preflight ok\n", "", 0),
            ("installer ok\n", "", 0),
            ("postflight ok\n", "", 0),
        ]

        ok = update_remote_server_task.run(str(server.id))

        self.assertTrue(ok)
        update_command = ssh.exec_command.call_args_list[1].args[0]
        self.assertIn("--mode=agent-lite", update_command)
        self.assertIn("SMSLY_NODE_QUEUE=", update_command)
        self.assertIn("MASTER_GATEWAY_SECRET=master-gateway", update_command)
        self.assertIn("SKIP_REBOOT=1", update_command)

        server.refresh_from_db()
        self.assertEqual(server.provision_status, ManagedServer.ProvisionStatus.DONE)
        self.assertEqual(server.gateway_secret, "master-gateway")
        self.assertEqual(
            server.provider_metadata["node_queue"],
            f"smsly-node-{server.id}",
        )

    @patch("apps.deployments.services.ssh_client.SSHClient")
    @patch("apps.notifications.tasks.dispatch_notification.delay")
    def test_update_task_dispatches_notification_on_success(self, dispatch_mock, ssh_cls):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="NotifySuccess",
            host="10.0.0.40",
            ssh_password="secret",
        )
        ssh = ssh_cls.return_value
        ssh.find_hosting_path.return_value = "/opt/smsly-hosting"
        ssh.exec_command.side_effect = [
            ("preflight ok\n", "", 0),
            ("installer ok\n", "", 0),
            ("postflight ok\n", "", 0),
        ]

        ok = update_remote_server_task.run(str(server.id))

        self.assertTrue(ok)
        dispatch_mock.assert_called_once_with(
            event_type='server_update_success',
            user_id=self.user.id,
            title=f"âœ… Server Update Succeeded: {server.name}",
            message=f"The update process for server '{server.name}' ({server.host}) completed successfully.",
            metadata={'server_id': str(server.id), 'server_name': server.name, 'host': server.host},
        )

    @patch("apps.deployments.services.ssh_client.SSHClient")
    @patch("apps.notifications.tasks.dispatch_notification.delay")
    def test_update_task_dispatches_notification_on_failure(self, dispatch_mock, ssh_cls):
        server = ManagedServer.objects.create(
            owner=self.user,
            name="NotifyFail",
            host="10.0.0.41",
            ssh_password="secret",
        )
        ssh = ssh_cls.return_value
        ssh.find_hosting_path.return_value = "/opt/smsly-hosting"
        ssh.exec_command.side_effect = [
            ("", "preflight failed\n", 1),
        ]

        ok = update_remote_server_task.run(str(server.id))

        self.assertFalse(ok)
        dispatch_mock.assert_called_once()
        kwargs = dispatch_mock.call_args.kwargs
        self.assertEqual(kwargs['event_type'], 'server_update_failed')
        self.assertEqual(kwargs['user_id'], self.user.id)
        self.assertIn("Server Update Failed", kwargs['title'])
        self.assertIn("NotifyFail", kwargs['message'])
        self.assertEqual(kwargs['metadata']['server_id'], str(server.id))
        self.assertEqual(kwargs['metadata']['server_name'], server.name)


class LiteAgentQueueTests(TestCase):
    @patch.dict(
        os.environ,
        {"MODE": "agent", "SMSLY_NODE_QUEUE": "smsly-node-test"},
        clear=False,
    )
    @patch("apps.deployments.tasks.smart_deploy_task.apply_async")
    def test_agent_enqueue_uses_dedicated_node_queue(self, apply_async_mock):
        enqueue_smart_deploy_task("dep-id", "provider-id", skip_review=True)

        apply_async_mock.assert_called_once()
        self.assertEqual(apply_async_mock.call_args.kwargs["queue"], "smsly-node-test")
        self.assertEqual(
            apply_async_mock.call_args.kwargs["kwargs"],
            {
                "deployment_id": "dep-id",
                "provider_id": "provider-id",
                "skip_review": True,
            },
        )

    @patch.dict(os.environ, {"MODE": "master"}, clear=False)
    @patch("apps.deployments.tasks.smart_deploy_task.delay")
    def test_full_install_enqueue_uses_standard_deploy_route(self, delay_mock):
        enqueue_smart_deploy_task("dep-id", "provider-id", skip_review=False)

        delay_mock.assert_called_once_with(
            deployment_id="dep-id",
            provider_id="provider-id",
            skip_review=False,
        )

    def test_auto_deployment_messages_skip_review(self):
        self.assertTrue(should_skip_review_for_commit_message("Platform update auto-redeploy"))
        self.assertTrue(should_skip_review_for_commit_message("Auto-Remediation: HEALTH_CHECK_FAIL"))
        self.assertTrue(should_skip_review_for_commit_message("[auto-fix] missing env"))
        self.assertFalse(should_skip_review_for_commit_message("Manual Trigger: HEAD"))

    @patch("apps.deployments.tasks.deploy.queue.enqueue_smart_deploy_task")
    def test_recover_stalled_auto_redeploy_preserves_auto_approval(self, enqueue_mock):
        user = User.objects.create_user(username="queue-user", password="password123")
        provider = CloudProvider.objects.create(
            name="queue-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        service = Service.objects.create(
            name="queue-service",
            owner=user,
            provider=provider,
        )
        deployment = Deployment.objects.create(
            service=service,
            status=Deployment.Status.QUEUED,
            commit_hash="abc1234567",
            commit_message="Platform update auto-redeploy",
        )

        result = recover_stalled_queued_deployments()

        self.assertEqual(result["queued"], 1)
        enqueue_mock.assert_called_once_with(
            deployment_id=str(deployment.id),
            provider_id=str(provider.id),
            skip_review=True,
        )

    @patch.dict(os.environ, {"SMSLY_ENABLE_FLEET_BUILD_LOCK": "true"}, clear=False)
    def test_fleet_build_lock_recovers_cancelled_owner(self):
        user = User.objects.create_user(username="lock-user", password="password123")
        provider = CloudProvider.objects.create(
            name="lock-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        service = Service.objects.create(
            name="lock-service",
            owner=user,
            provider=provider,
        )
        stale = Deployment.objects.create(
            service=service,
            status=Deployment.Status.CANCELLED,
            commit_hash="stale1234",
        )
        current = Deployment.objects.create(
            service=service,
            status=Deployment.Status.BUILDING,
            commit_hash="fresh1234",
        )
        cache.set("smsly_fleet_build_lock", str(stale.id), timeout=300)

        with fleet_build_lock(current):
            self.assertEqual(cache.get("smsly_fleet_build_lock"), str(current.id))

        self.assertIsNone(cache.get("smsly_fleet_build_lock"))


class SSHClientCallbackTests(TestCase):
    @patch("paramiko.SSHClient")
    def test_exec_command_streams_output_to_callback(self, mock_paramiko_client):
        from apps.deployments.services.ssh_client import SSHClient

        # Setup mock paramiko client and channel
        mock_ssh = mock_paramiko_client.return_value
        mock_transport = MagicMock()
        mock_ssh.get_transport.return_value = mock_transport
        mock_transport.is_active.return_value = True

        stdin = MagicMock()
        stdout = MagicMock()
        stderr = MagicMock()
        mock_ssh.exec_command.return_value = (stdin, stdout, stderr)

        # Mock paramiko channel methods to return output dynamically
        channel = stdout.channel
        channel.recv_ready.side_effect = [True, False, True, False] + [False] * 10
        channel.recv.side_effect = [b"hello", b" world"]
        channel.recv_stderr_ready.side_effect = [True, False, False] + [False] * 10
        channel.recv_stderr.side_effect = [b"some error"]
        channel.exit_status_ready.side_effect = [False, True]
        channel.recv_exit_status.return_value = 0

        client = SSHClient(ip="127.0.0.1", password="test")
        client.client = mock_ssh

        callback_calls = []
        def test_callback(out, err):
            callback_calls.append((out, err))

        out, err, code = client.exec_command("echo", callback=test_callback)

        self.assertEqual(code, 0)
        self.assertEqual(out, "hello world")
        self.assertEqual(err, "some error")
        self.assertEqual(len(callback_calls), 2)
        self.assertEqual(callback_calls[0], ("hello", "some error"))
        self.assertEqual(callback_calls[1], (" world", ""))

