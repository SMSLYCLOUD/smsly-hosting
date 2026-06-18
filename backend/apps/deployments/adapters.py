"""Adapters module."""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom account adapter — no overrides currently, kept for future hooks."""


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom social account adapter that applies provider-specific callback URL overrides."""

    def get_connect_redirect_url(self, request, socialaccount):
        return settings.LOGIN_REDIRECT_URL

    @property
    def callback_url_overrides(self) -> dict:
        """Map provider IDs to env-configured callback URL overrides."""
        return {
            'github': getattr(settings, 'GITHUB_OAUTH_CALLBACK_URL', None),
            'gitlab': getattr(settings, 'GITLAB_OAUTH_CALLBACK_URL', None),
            'bitbucket_oauth2': getattr(settings, 'BITBUCKET_OAUTH_CALLBACK_URL', None),
            'google': getattr(settings, 'GOOGLE_OAUTH_CALLBACK_URL', None),
        }
