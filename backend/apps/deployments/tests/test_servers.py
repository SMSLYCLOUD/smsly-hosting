"""Tests for ManagedServer model and views."""

from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from ..models_servers import ManagedServer

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
