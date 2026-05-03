# pylint: disable=invalid-name
"""Tests for ManagedServer model and views."""

from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from ..models_servers import ManagedServer
from ..views_servers import _build_remote_headers

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

    @patch("apps.deployments.views_servers.requests.get")
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

    @patch("apps.deployments.views_servers.requests.get")
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
        self.assertEqual(resp.data["api_url"], "http://10.0.0.6")
        self.assertEqual(resp.data["server_version"], "2.0.0")
        self.assertEqual(resp.data["services_count"], 2)

        server.refresh_from_db()
        self.assertEqual(server.api_url, "http://10.0.0.6")

    @patch("apps.deployments.views_servers.requests.get")
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
        self.assertEqual(resp.data["api_url"], "http://10.0.0.7")

    @patch("apps.deployments.views_servers.requests.get")
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

    @patch("apps.deployments.views_servers.requests.get")
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

    @patch("apps.deployments.views_servers.requests.get")
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

    @patch("apps.deployments.views_servers.requests.get")
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

    @patch("apps.deployments.views_servers.requests.get")
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

    @patch("apps.deployments.views_servers.requests.get")
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

    @patch("apps.deployments.views_servers.requests.get")
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
