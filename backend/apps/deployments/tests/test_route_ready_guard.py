"""Tests for deployment route readiness guardrails."""

import requests
from django.test import SimpleTestCase

from apps.deployments.tasks.deployment.tasks_deploy_remote import _route_misroute_reason


def _response(body: str = "", headers: dict | None = None, status_code: int = 200):
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")  # pylint: disable=protected-access
    response.headers.update(headers or {})
    return response


class RouteReadyGuardTests(SimpleTestCase):
    """Ensure platform fallback responses are not accepted as app readiness."""

    def test_control_plane_header_is_misroute(self):
        reason = _route_misroute_reason(
            _response(headers={"X-SMSLY-Control-Plane": "true"})
        )

        self.assertIn("control-plane", reason)

    def test_route_fallback_header_is_misroute(self):
        reason = _route_misroute_reason(
            _response(headers={"X-SMSLY-Route-Fallback": "true"}, status_code=503)
        )

        self.assertIn("route fallback", reason)

    def test_platform_homepage_markers_are_misroute(self):
        reason = _route_misroute_reason(
            _response("Deployment Previews with Global Edge Routing for The Sovereign PaaS")
        )

        self.assertIn("platform homepage", reason)

    def test_normal_app_response_is_allowed(self):
        reason = _route_misroute_reason(_response("hello from my service"))

        self.assertEqual(reason, "")
