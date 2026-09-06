"""Tests for the socialApps sign-in/sign-up review fixes.

1. Public provider availability (GET /api/v1/oauth/providers/):
   AllowAny, booleans only, never secrets.
2. Callback URL override gating: the SPA override applies ONLY to
   the ?process=connect (account linking) flow — never to SSO
   login/signup, where allauth must receive the code itself.
3. Verified-email login policy is enabled (email-collision UX).
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

User = get_user_model()


def _client(**kwargs):
    kwargs.setdefault("SERVER_NAME", "grid.smsly.cloud")
    return Client(**kwargs)


class OAuthProvidersPublicTests(TestCase):
    URL = "/api/v1/oauth/providers/"

    def test_anonymous_can_read(self):
        resp = _client().get(self.URL)
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        data = resp.json()
        for key in ("github", "google", "gitlab", "bitbucket_oauth2"):
            self.assertIn(key, data)
            self.assertIsInstance(data[key], bool)

    def test_reflects_configured_apps(self):
        from allauth.socialaccount.models import SocialApp

        before = _client().get(self.URL).json()
        self.assertFalse(before["gitlab"])
        SocialApp.objects.create(
            provider="gitlab", name="GitLab",
            client_id="id", secret="secret",
        )
        after = _client().get(self.URL).json()
        self.assertTrue(after["gitlab"])
        # Unrelated providers unaffected
        self.assertEqual(after["github"], before["github"])

    def test_no_secrets_leaked(self):
        body = _client().get(self.URL).content.decode()
        self.assertNotIn("secret", body.lower())
        self.assertNotIn("client_id", body.lower())


class CallbackOverrideGatingTests(TestCase):
    """allauth 65 builds the OAuth2 callback URL on the provider's own
    OAuth2Adapter (reverse("<provider>_callback")) — there is NO
    adapter-level get_callback_url hook anymore. These tests pin that
    reality so nobody re-adds a dead override, and prove the live SSO
    entry point redirects to the provider with allauth's own callback.
    """

    def test_no_stale_callback_override_on_adapter(self):
        from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
        from apps.core.adapters import CustomSocialAccountAdapter

        self.assertFalse(
            hasattr(DefaultSocialAccountAdapter, "get_callback_url"),
            "allauth base has no get_callback_url; an override would be dead code",
        )
        self.assertNotIn(
            "get_callback_url",
            CustomSocialAccountAdapter.__dict__,
            "stale adapter-level override — allauth never calls it",
        )

    def test_connect_redirect_goes_to_spa(self):
        from django.conf import settings
        from apps.core.adapters import CustomSocialAccountAdapter

        adapter = CustomSocialAccountAdapter()
        req = mock.Mock()
        url = adapter.get_connect_redirect_url(req, mock.Mock())
        self.assertEqual(url, settings.LOGIN_REDIRECT_URL)

    def test_github_login_redirects_to_provider_with_allauth_callback(self):
        from allauth.socialaccount.models import SocialApp

        SocialApp.objects.create(
            provider="github", name="GitHub",
            client_id="test-client-id", secret="test-secret",
        )
        resp = _client().get("/accounts/github/login/")
        self.assertEqual(resp.status_code, 302, resp.content[:200])
        location = resp["Location"]
        self.assertIn("https://github.com/login/oauth/authorize", location)
        # The provider must send the code back to ALLATH's callback —
        # that is where state is validated and the session established.
        self.assertIn("/accounts/github/login/callback/", location)
        self.assertIn("state=", location)


class EmailAuthPolicyTests(TestCase):
    def test_verified_email_login_enabled(self):
        # A GitHub-verified email matching an existing local account
        # must log into it instead of dead-ending on "already exists".
        from django.conf import settings
        self.assertTrue(getattr(settings, "SOCIALACCOUNT_EMAIL_AUTHENTICATION", False))

    def test_enumeration_protection_stays_on(self):
        # Effective value (allauth default is True in v65+; pinned here
        # so a future default flip is caught).
        from allauth.account import app_settings as acc_settings
        self.assertTrue(acc_settings.PREVENT_ENUMERATION)
