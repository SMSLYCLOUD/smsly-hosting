"""Route readiness helpers for local Traefik deployments."""

import requests
from django.test import SimpleTestCase

from apps.deployments.tasks.deployment.tasks_deploy_remote import _is_traefik_not_ready


def _build_response(status_code: int, body: str, headers=None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response._content = body.encode("utf-8")
    response.headers = requests.structures.CaseInsensitiveDict(headers or {})
    return response


class TraefikReadinessHelpersTests(SimpleTestCase):
    def test_detects_default_traefik_404_without_server_header(self):
        response = _build_response(
            404,
            "404 page not found",
            {
                "Content-Type": "text/plain; charset=utf-8",
                "X-Content-Type-Options": "nosniff",
            },
        )

        self.assertTrue(_is_traefik_not_ready(response))

    def test_does_not_match_framework_html_404(self):
        response = _build_response(
            404,
            "<html><body>Not Found</body></html>",
            {
                "Content-Type": "text/html; charset=utf-8",
            },
        )

        self.assertFalse(_is_traefik_not_ready(response))

    def test_does_not_match_non_404(self):
        response = _build_response(
            200,
            "ok",
            {
                "Content-Type": "text/plain; charset=utf-8",
            },
        )

        self.assertFalse(_is_traefik_not_ready(response))

