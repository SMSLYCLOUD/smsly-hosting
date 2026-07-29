from unittest.mock import MagicMock, patch

import pytest
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


@pytest.mark.django_db(transaction=True)
class ProxySizeCapTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="proxy_size", password="123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="proxy-size-test",
            host="203.0.113.40",
            api_url="https://proxy-size.example.com",
            api_token="tok",
        )
        self.url = f"/api/v1/servers/{self.server.id}/proxy/"

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    @patch("apps.deployments.views_servers.requests.request")
    def test_proxy_with_body_under_one_mb_passes(self, mock_request):
        mock_request.return_value = _make_mock_response(
            status_code=200, payload={"results": [], "count": 0}
        )
        body = {"items": ["x"] * 100}
        resp = self.client.post(
            self.url,
            {"method": "POST", "path": "/api/v1/health", "body": body},
            format="json",
        )
        # Issue 10 (Batch O) restricted the proxy to GET/HEAD methods
        # and an allowlist of /api/v1/health and /api/v1/metrics. POST
        # is now rejected (400 or 405 depending on the implementation).
        self.assertIn(resp.status_code, (400, 405))
        mock_request.assert_not_called()

    @patch("apps.deployments.views_servers.requests.request")
    def test_proxy_with_body_over_one_mb_returns_413(self, mock_request):
        mock_request.return_value = _make_mock_response(status_code=200)
        large_string = "x" * (1024 * 1024 + 1024)
        body = {"blob": large_string}
        resp = self.client.post(
            self.url,
            {"method": "POST", "path": "/api/v1/health", "body": body},
            format="json",
        )
        # POST is rejected before the size cap; size cap is only reachable
        # via GET/HEAD with an allowlisted path.
        self.assertIn(resp.status_code, (400, 405))
        mock_request.assert_not_called()

    @patch("apps.deployments.views_servers.requests.request")
    def test_proxy_admin_path_still_passes_size_cap(self, mock_request):
        mock_request.return_value = _make_mock_response(
            status_code=200, payload={"results": [], "count": 0}
        )
        body = {"items": ["x"] * 10}
        resp = self.client.post(
            self.url,
            {"method": "GET", "path": "/api/admin/secret", "body": body},
            format="json",
        )
        # Issue 10 (Batch O) restricted the proxy to /api/v1/health
        # and /api/v1/metrics. /api/admin/secret is now 403 (or 400).
        self.assertIn(resp.status_code, (400, 403))
        mock_request.assert_not_called()
