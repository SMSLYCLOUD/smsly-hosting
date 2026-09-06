"""Adapters module."""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings


class CustomAccountAdapter(DefaultAccountAdapter):
    """Custom account adapter — no overrides currently, kept for future hooks."""


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Custom social account adapter that applies provider-specific callback URL overrides.

    Only applies the override for the **account linking** flow, identified
    by allauth's ``?process=connect`` marker — never for the **SSO
    login/signup** flow, where allauth must receive the code at its own
    callback URL to validate state and establish the session.
    """

    def get_connect_redirect_url(self, request, _socialaccount):
        return settings.LOGIN_REDIRECT_URL

    def get_callback_url(self, request, provider):
        # The override applies ONLY to the account-linking ("connect")
        # flow, which allauth marks with ?process=connect. Being merely
        # authenticated is NOT sufficient: an already-logged-in user who
        # clicks "Sign in with GitHub" again starts a *login* flow, and
        # allauth must receive the code at its own callback URL to
        # validate state. Sending the provider to an SPA page instead
        # strands the code where nothing exchanges it (dead flow).
        if request.GET.get('process') != 'connect':
            return super().get_callback_url(request, provider)

        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return super().get_callback_url(request, provider)

        # User IS authenticated in a connect flow — this is account
        # linking. Apply provider-specific callback URL overrides
        # (SPA callback pages).
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
