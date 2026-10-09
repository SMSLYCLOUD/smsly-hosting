"""View tests for POST /api/v1/crowdsec/unban/ (no docker needed).

Pins the fail-closed contract the Intelligence Security Intel tab
depends on:
* route exists and revokes via the crowdsec service (mocked here;
  services.py covers the real `cscli decisions delete` invocation).
* staff-only: non-staff authenticated users get 403.
* invalid IP/CIDR is a 400, not a 500.
* backend/cscli failures return a generic message (no stderr or
  exception text leaks to clients).
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.crowdsec.views import crowdsec_unban


class CrowdSecUnbanViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="cs-admin", password="password123", is_staff=True)
        self.user = User.objects.create_user(
            username="cs-user", password="password123")
        self.factory = APIRequestFactory()

    def _post(self, user, data):
        req = self.factory.post("/api/v1/crowdsec/unban/", data,
                                format="json")
        force_authenticate(req, user=user)
        return crowdsec_unban(req)

    def test_non_staff_forbidden(self):
        resp = self._post(self.user, {"ip": "1.2.3.4"})
        self.assertEqual(resp.status_code, 403)

    def test_missing_ip_is_400(self):
        resp = self._post(self.admin, {})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.data)

    def test_invalid_ip_is_400_not_500(self):
        # Real service validates via ipaddress before touching docker.
        resp = self._post(self.admin, {"ip": "not-an-ip"})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(str(resp.data["error"]).startswith("invalid "))

    def test_success_returns_removed(self):
        mock_service = MagicMock()
        mock_service.unban.return_value = {
            "status": "removed", "ip": "1.2.3.4"}
        with patch("apps.crowdsec.views.get_crowdsec_service",
                    return_value=mock_service):
            resp = self._post(self.admin, {"ip": "1.2.3.4"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "removed")
        mock_service.unban.assert_called_once_with("1.2.3.4", "Ip")

    def test_service_failure_is_generic_500(self):
        mock_service = MagicMock()
        mock_service.unban.return_value = {
            "error": "cscli boom: LAPI token hunter2"}
        with patch("apps.crowdsec.views.get_crowdsec_service",
                    return_value=mock_service):
            resp = self._post(self.admin, {"ip": "1.2.3.4"})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.data, {"error": "unban failed"})

    def test_unexpected_exception_is_generic_500(self):
        with patch("apps.crowdsec.views.get_crowdsec_service",
                   side_effect=RuntimeError("db password=hunter2")):
            resp = self._post(self.admin, {"ip": "1.2.3.4"})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.data, {"error": "internal error"})
