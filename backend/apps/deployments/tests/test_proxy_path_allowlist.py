"""
Regression tests for the proxy SSRF amplifier fix (Issue 10).

Covers:
  1. Methods other than GET/HEAD are rejected with 405.
  2. Paths outside the allowlist are rejected with 403.
  3. api_url whose hostname does not match server.host is rejected.
  4. Allowlisted paths against the correct host still succeed.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.servers import ManagedServer


def _make_mock_response(status_code=200, payload=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {"ok": True}
    response.text = ""
    return response


class ProxyAllowlistTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="proxy-allow", password="p"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="proxy-allow-server",
            host="203.0.113.50",
            api_url="https://203.0.113.50",
            api_token="tok",
        )
        self.url = f"/api/v1/servers/{self.server.id}/proxy/"

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_post_method_rejected(self, mock_request):
        mock_request.return_value = _make_mock_response(200)
        resp = self.client.post(
            self.url,
            {"method": "POST", "path": "/api/v1/health", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 405)
        mock_request.assert_not_called()

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_delete_method_rejected(self, mock_request):
        mock_request.return_value = _make_mock_response(200)
        resp = self.client.post(
            self.url,
            {"method": "DELETE", "path": "/api/v1/health", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 405)
        mock_request.assert_not_called()

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_services_path_rejected_as_not_in_allowlist(self, mock_request):
        mock_request.return_value = _make_mock_response(200)
        resp = self.client.post(
            self.url,
            {"method": "GET", "path": "/api/v1/services/", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        mock_request.assert_not_called()

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_deployments_path_rejected_as_not_in_allowlist(self, mock_request):
        mock_request.return_value = _make_mock_response(200)
        resp = self.client.post(
            self.url,
            {"method": "GET", "path": "/api/v1/deployments/", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        mock_request.assert_not_called()

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_admin_path_rejected_as_not_in_allowlist(self, mock_request):
        mock_request.return_value = _make_mock_response(200)
        resp = self.client.post(
            self.url,
            {"method": "GET", "path": "/api/admin/secret", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        mock_request.assert_not_called()

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_api_url_with_mismatched_hostname_rejected(self, mock_request):
        mock_request.return_value = _make_mock_response(200)
        self.server.api_url = "https://attacker.example.com"
        self.server.save()
        resp = self.client.post(
            self.url,
            {"method": "GET", "path": "/api/v1/health", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_request.assert_not_called()

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_allowlisted_health_path_with_matching_host_succeeds(self, mock_request):
        mock_request.return_value = _make_mock_response(
            status_code=200, payload={"status": "ok"}
        )
        resp = self.client.post(
            self.url,
            {"method": "GET", "path": "/api/v1/health", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

    @patch("apps.deployments.views.server.proxy.requests.request")
    def test_head_method_against_metrics_succeeds(self, mock_request):
        mock_request.return_value = _make_mock_response(status_code=200)
        resp = self.client.post(
            self.url,
            {"method": "HEAD", "path": "/api/v1/metrics", "body": None},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
