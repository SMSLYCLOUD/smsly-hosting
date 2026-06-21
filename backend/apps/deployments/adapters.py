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
        Build the OAuth callback URL for django-allauth login.

        The Settings UI 'Connect GitHub/Google/...' flow uses a SEPARATE
        callback path (/auth/<provider>/callback → frontend → POST to
        /api/v1/integrations/...).  This method is ONLY for the allauth
        login dance — we always use the standard /accounts/<provider>/
        login/callback/ path, which is already routed to the backend by
        the Caddyfile.

        Provider-specific env vars (GITHUB_OAUTH_CALLBACK_URL, etc.) are
        for the custom integration only and are intentionally NOT read here.
        """
        provider_id = getattr(provider, 'provider_id', None) or str(provider)

        # Auto-generate from the platform domain stored in the DB.
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
