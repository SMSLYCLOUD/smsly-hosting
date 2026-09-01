"""Regression tests for the git-provider webhook exemption in
SecurityMiddleware.

Bug (found live 2026-09-01): the Zero-Trust middleware enforced its
gateway HMAC V2 on EVERY /api/ path. The git webhook receivers live at
/api/v1/services/webhook/{github,gitlab,bitbucket}/ — not under the
already-exempt /api/v1/webhooks/ prefix — so GitHub's push events were
403'd by the middleware BEFORE the view's own X-Hub-Signature-256
verification could run. Push-to-deploy never fired, regardless of the
webhook secret being correctly configured on both sides.
"""
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.core.middleware.security import SecurityMiddleware


class GitWebhookExemptionTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = SecurityMiddleware(lambda r: mock.Mock(status_code=200))

    def _is_enforced(self, path):
        request = self.factory.post(path, data="{}", content_type="application/json")
        # No auth headers/cookies — simulate a raw GitHub delivery.
        request.user = mock.Mock(is_authenticated=False)
        return self.middleware._should_verify_signature(request)

    @override_settings(DEBUG=False)
    def test_github_webhook_path_is_exempt(self):
        self.assertFalse(
            self._is_enforced("/api/v1/webhooks/github/"),
            "GitHub webhook receiver must be exempt from gateway HMAC — "
            "GitHub cannot know GATEWAY_SECRET and verifies its own "
            "X-Hub-Signature-256 inside the view.",
        )

    @override_settings(DEBUG=False)
    def test_gitlab_webhook_path_is_exempt(self):
        self.assertFalse(self._is_enforced("/api/v1/services/webhook/gitlab/"))

    @override_settings(DEBUG=False)
    def test_bitbucket_webhook_path_is_exempt(self):
        self.assertFalse(self._is_enforced("/api/v1/services/webhook/bitbucket/"))

    @override_settings(DEBUG=False)
    def test_unrelated_api_paths_still_enforced(self):
        # The exemption must be narrowly scoped to the webhook receiver —
        # generic API routes still require the gateway/zero-trust checks.
        self.assertTrue(self._is_enforced("/api/v1/services/"))
        self.assertTrue(self._is_enforced("/api/v1/deployments/"))

    @override_settings(DEBUG=False)
    def test_webhook_subpaths_not_open_endpoints(self):
        # The prefix covers only /api/v1/services/webhook/* — adjacent
        # service routes are NOT exempt.
        self.assertTrue(self._is_enforced("/api/v1/services/webhookery/"))
