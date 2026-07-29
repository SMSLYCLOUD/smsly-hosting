"""Adapters module."""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom account adapter — no overrides currently, kept for future hooks."""


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom social account adapter that applies provider-specific callback URL overrides.

    Only applies the override for the **account linking** flow (user is already
    authenticated).  For the **SSO login** flow (user is NOT authenticated), the
    default allauth callback URL is used so that allauth can establish the session.
    """

    def get_connect_redirect_url(self, request, _socialaccount):
        return settings.LOGIN_REDIRECT_URL

    def get_callback_url(self, request, provider):
        # If the user is NOT authenticated, this is the SSO login flow.
        # Let allauth handle the callback normally so it can create the session.
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return super().get_callback_url(request, provider)

        # User IS authenticated — this is the account linking flow.
        # Apply provider-specific callback URL overrides (SPA callback pages).
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
