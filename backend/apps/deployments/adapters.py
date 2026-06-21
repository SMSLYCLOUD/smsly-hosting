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
        Build the OAuth callback URL for django-allauth login & connect.

        Uses the /auth/<provider>/callback path because operators
        register their OAuth apps with that URL (e.g. GitHub app
        callback = https://grid.smsly.cloud/auth/github/callback).
        The Caddyfile routes /auth/<provider>/callback* to the backend
        so allauth's callback view handles the code exchange directly.
        """
        provider_id = getattr(provider, 'provider_id', None) or str(provider)

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
