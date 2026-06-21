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
        If none is set, auto-generate from PlatformConfig.domain so the operator
        never has to manually set GITHUB_OAUTH_CALLBACK_URL in .env.
        Falls back to the standard allauth reverse('PROVIDER_callback') pattern.
        """
        provider_id = provider.id if hasattr(provider, 'id') else str(provider)
        overrides = {
            'github': getattr(settings, 'GITHUB_OAUTH_CALLBACK_URL', None),
            'gitlab': getattr(settings, 'GITLAB_OAUTH_CALLBACK_URL', None),
            'bitbucket_oauth2': getattr(settings, 'BITBUCKET_OAUTH_CALLBACK_URL', None),
            'google': getattr(settings, 'GOOGLE_OAUTH_CALLBACK_URL', None),
        }
        override = overrides.get(provider_id)
        if override:
            return override

        # Auto-generate from the platform domain stored in the DB.
        # This makes OAuth callback URLs zero-config: the operator only
        # sets the platform domain once in the Settings UI and the same
        # /auth/<provider>/callback path (already routed by Caddy) works
        # for all providers.
        try:
            from .models_core import PlatformConfig
            cfg = PlatformConfig.load()
            domain = (getattr(cfg, 'domain', '') or '').strip()
        except Exception:
            domain = ''
        if domain:
            protocol = 'https' if getattr(cfg, 'use_ssl', True) else 'http'
            return f"{protocol}://{domain}/auth/{provider_id}/callback"

        return super().get_callback_url(request, provider)
