"""Adapters module."""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom account adapter — no overrides currently, kept for future hooks."""


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom social account adapter.

    NOTE (allauth 65): do NOT override ``get_callback_url`` here — it
    does not exist on ``DefaultSocialAccountAdapter`` anymore. The
    OAuth2 callback URL is built by the provider's own
    ``OAuth2Adapter.get_callback_url`` (``reverse("<provider>_callback")``),
    so an adapter-level override is dead code that silently never runs.
    A previous revision carried such an override gated on the
    authenticated user / ``?process=connect``; it was removed once
    live introspection proved it unreachable. If per-flow callback
    routing is ever needed again, it must hook the provider adapter
    (e.g. a custom ``OAuth2Adapter`` subclass per provider), not this
    class.
    """

    def get_connect_redirect_url(self, request, _socialaccount):
        return settings.LOGIN_REDIRECT_URL
