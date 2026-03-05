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
