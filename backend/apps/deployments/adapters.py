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

    def get_callback_url(self, request, provider):
        """
        Build the OAuth callback URL, checking provider-specific env overrides first.
        Falls back to the standard allauth reverse('PROVIDER_callback') pattern.

        NOTE: allauth OAuth adapters expose the provider slug as
        ``provider_id`` (e.g. 'github'), NOT as ``.id``. The original
        implementation used ``hasattr(provider, 'id')`` which always
        fell through to ``str(provider)``, producing a garbage Python
        repr that never matched the overrides dict.
        """
        provider_id = getattr(provider, 'provider_id', None) or str(provider)
        overrides = {
            'github': getattr(settings, 'GITHUB_OAUTH_CALLBACK_URL', None),
            'gitlab': getattr(settings, 'GITLAB_OAUTH_CALLBACK_URL', None),
            'bitbucket_oauth2': getattr(settings, 'BITBUCKET_OAUTH_CALLBACK_URL', None),
            'google': getattr(settings, 'GOOGLE_OAUTH_CALLBACK_URL', None),
        }
        override = overrides.get(provider_id)
        if override:
            return override
        return super().get_callback_url(request, provider)
