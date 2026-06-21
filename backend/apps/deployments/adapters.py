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

        Uses the standard /accounts/<provider>/login/callback/ path
        because the frontend login/register pages link to
        /accounts/<provider>/login/ and the Settings UI instructs
        operators to register that URL with their OAuth app.
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
            return f"{protocol}://{domain}/accounts/{provider_id}/login/callback/"

        return super().get_callback_url(request, provider)
