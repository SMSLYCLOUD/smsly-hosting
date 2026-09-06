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
    def _adapter(self):
        from apps.core.adapters import CustomSocialAccountAdapter
        return CustomSocialAccountAdapter()

    def _request(self, authenticated, process=None):
        from django.test import RequestFactory
        rf = RequestFactory()
        query = f"?process={process}" if process else ""
        req = rf.get(f"/accounts/github/login/callback/{query}")
        req.user = mock.Mock(
            is_authenticated=authenticated,
            spec=["is_authenticated"],
        )
        return req

    def _provider(self, provider_id="github"):
        return mock.Mock(provider_id=provider_id)

    def test_login_flow_uses_stock_callback_even_when_authenticated(self):
        # Logged-in user re-clicking "Sign in with GitHub" starts a
        # LOGIN flow (no ?process=connect) — allauth must get the code.
        adapter = self._adapter()
        with mock.patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_callback_url",
            return_value="https://grid.smsly.cloud/accounts/github/login/callback/",
        ) as stock:
            url = adapter.get_callback_url(
                self._request(authenticated=True), self._provider()
            )
        stock.assert_called_once()
        self.assertIn("/accounts/github/login/callback/", url)

    def test_connect_flow_uses_override(self):
        adapter = self._adapter()
        # With ?process=connect and an override configured, the SPA URL wins
        with mock.patch.object(
            __import__("django.conf", fromlist=["settings"]).settings,
            "GITHUB_OAUTH_CALLBACK_URL",
            "https://grid.smsly.cloud/auth/github/callback",
            create=True,
        ):
            url = adapter.get_callback_url(
                self._request(authenticated=True, process="connect"),
                self._provider(),
            )
        self.assertEqual(url, "https://grid.smsly.cloud/auth/github/callback")

    def test_connect_flow_without_override_falls_back(self):
        adapter = self._adapter()
        with mock.patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_callback_url",
            return_value="https://grid.smsly.cloud/accounts/github/login/callback/",
        ) as stock:
            url = adapter.get_callback_url(
                self._request(authenticated=True, process="connect"),
                self._provider(),
            )
        stock.assert_called_once()
        self.assertIn("/accounts/github/login/callback/", url)

    def test_anonymous_always_stock(self):
        adapter = self._adapter()
        with mock.patch(
            "allauth.socialaccount.adapter.DefaultSocialAccountAdapter.get_callback_url",
            return_value="https://grid.smsly.cloud/accounts/github/login/callback/",
        ) as stock:
            url = adapter.get_callback_url(
                self._request(authenticated=False, process="connect"),
                self._provider(),
            )
        stock.assert_called_once()
        self.assertIn("/accounts/github/login/callback/", url)


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
