"""Regression tests for configured GitLab/Bitbucket webhook authentication."""
import hashlib
import hmac
import os
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from apps.cloud.services.bitbucket_webhooks import setup_bitbucket_webhook
from apps.cloud.services.gitlab_webhooks import setup_gitlab_webhook
from apps.cloud.webhooks.bitbucket import BitbucketWebhookHandler
from apps.cloud.webhooks.gitlab import GitLabWebhookHandler
from apps.deployments.models.core import PlatformConfig
from apps.deployments.views.webhooks import BitbucketWebhookView, GitLabWebhookView


@override_settings(GITLAB_WEBHOOK_SECRET="", BITBUCKET_WEBHOOK_SECRET="")
class WebhookSecretResolutionTests(SimpleTestCase):
    providers = (
        ("gitlab", GitLabWebhookView, GitLabWebhookHandler),
        ("bitbucket", BitbucketWebhookView, BitbucketWebhookHandler),
    )

    def setUp(self):
        self.factory = APIRequestFactory()
        self.config = PlatformConfig(
            gitlab_webhook_secret="database-secret",
            bitbucket_webhook_secret="database-secret",
        )
        self.loader = patch.object(PlatformConfig, "load", return_value=self.config)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        env = patch.dict(os.environ, {"GITLAB_WEBHOOK_SECRET": "", "BITBUCKET_WEBHOOK_SECRET": ""})
        env.start()
        self.addCleanup(env.stop)

    def request(self, provider, secret="database-secret", body=b"{}", signed_body=None):
        if provider == "gitlab":
            headers = {"HTTP_X_GITLAB_TOKEN": secret, "HTTP_X_GITLAB_EVENT": "Test Hook"}
        else:
            digest = hmac.new(secret.encode(), signed_body if signed_body is not None else body, hashlib.sha256).hexdigest()
            headers = {"HTTP_X_HUB_SIGNATURE": "sha256=" + digest, "HTTP_X_EVENT_KEY": "test:event"}
        return self.factory.post("/", data=body, content_type="application/json", **headers)

    def test_database_configured_receivers_use_real_verifiers(self):
        for provider, view, _ in self.providers:
            with self.subTest(provider=provider):
                response = view.as_view()(self.request(provider))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.data["triggered"])

    @override_settings(GITLAB_WEBHOOK_SECRET="stale-settings", BITBUCKET_WEBHOOK_SECRET="stale-settings")
    def test_database_secret_overrides_settings_for_receivers(self):
        for provider, view, _ in self.providers:
            with self.subTest(provider=provider):
                self.assertEqual(view.as_view()(self.request(provider)).status_code, 200)
                self.assertEqual(view.as_view()(self.request(provider, "stale-settings")).status_code, 403)

    def test_unconfigured_secrets_reject_even_valid_empty_key_signatures(self):
        self.config.gitlab_webhook_secret = ""
        self.config.bitbucket_webhook_secret = ""
        for provider, view, handler in self.providers:
            with self.subTest(provider=provider):
                request = self.request(provider, "")
                self.assertFalse(handler().verify_signature(request))
                self.assertEqual(view.as_view()(request).status_code, 503)

    def test_environment_secret_works_without_settings_assignment(self):
        self.config.gitlab_webhook_secret = ""
        self.config.bitbucket_webhook_secret = ""
        with patch.dict(os.environ, {"GITLAB_WEBHOOK_SECRET": "env-secret", "BITBUCKET_WEBHOOK_SECRET": "env-secret"}):
            for provider, view, _ in self.providers:
                with self.subTest(provider=provider):
                    self.assertEqual(view.as_view()(self.request(provider, "env-secret")).status_code, 200)

    @override_settings(GITLAB_WEBHOOK_SECRET="settings-secret", BITBUCKET_WEBHOOK_SECRET="settings-secret")
    def test_settings_fallback_works_when_database_and_environment_are_empty(self):
        self.config.gitlab_webhook_secret = ""
        self.config.bitbucket_webhook_secret = ""
        for provider, view, _ in self.providers:
            with self.subTest(provider=provider):
                self.assertEqual(view.as_view()(self.request(provider, "settings-secret")).status_code, 200)

    @override_settings(GITLAB_WEBHOOK_SECRET="stale-settings", BITBUCKET_WEBHOOK_SECRET="stale-settings")
    def test_config_load_failure_fails_closed(self):
        with patch.object(PlatformConfig, "load", side_effect=RuntimeError("unavailable")):
            for provider, view, handler in self.providers:
                with self.subTest(provider=provider):
                    self.assertFalse(handler().verify_signature(self.request(provider, "stale-settings")))
                    self.assertEqual(view.as_view()(self.request(provider, "stale-settings")).status_code, 503)

    def test_bitbucket_verifies_exact_body_and_rejects_missing_or_malformed_headers(self):
        handler = BitbucketWebhookHandler()
        self.assertTrue(handler.verify_signature(self.request("bitbucket")))
        self.assertFalse(handler.verify_signature(self.request("bitbucket", body=b"{ }", signed_body=b"{}")))
        for value in ("", "sha1=bad", "sha256=bad", "sha256=\u00e9"):
            with self.subTest(signature=value):
                request = self.request("bitbucket")
                request.META["HTTP_X_HUB_SIGNATURE"] = value
                self.assertFalse(handler.verify_signature(request))

    def test_gitlab_rejects_missing_incorrect_and_non_ascii_tokens(self):
        for token in ("", "wrong", "\u00e9"):
            with self.subTest(token=token):
                self.assertFalse(GitLabWebhookHandler().verify_signature(self.request("gitlab", token)))

    def test_setup_passes_database_secret_to_each_provider(self):
        cases = (
            ("gitlab", setup_gitlab_webhook, "https://gitlab.com/team/repo", "token"),
            ("bitbucket", setup_bitbucket_webhook, "https://bitbucket.org/team/repo", "secret"),
        )
        for provider, setup, url, field in cases:
            with self.subTest(provider=provider):
                module = "apps.cloud.services." + provider + "_webhooks"
                sent = []

                def post(url, **kwargs):
                    sent.append(kwargs["json"])
                    from types import SimpleNamespace
                    return SimpleNamespace(ok=True)

                with patch(module + "._get_" + provider + "_token", return_value="oauth-test-token"), patch(module + ".requests.get") as get, patch(module + ".requests.post", side_effect=post):
                    get.return_value.ok = True
                    if provider == "gitlab":
                        from unittest.mock import Mock
                        get.side_effect = [Mock(**{"json.return_value": {"id": 1}}), Mock(ok=True, **{"json.return_value": []})]
                    else:
                        get.return_value.json.return_value = {"values": []}
                    self.assertTrue(setup(object(), url))
                self.assertEqual(len(sent), 1)
                self.assertEqual(sent[0][field], "database-secret")
