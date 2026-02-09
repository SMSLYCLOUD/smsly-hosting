"""Adapters module."""
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from rest_framework.authtoken.models import Token


class CustomAccountAdapter(DefaultAccountAdapter):
    def get_login_redirect_url(self, request):
        # Get the base redirect URL (usually LOGIN_REDIRECT_URL from settings)
        url = super().get_login_redirect_url(request)

        # If the user is authenticated, generate/get their DRF token
        if request.user.is_authenticated:
            token, created = Token.objects.get_or_create(user=request.user)
            # Append the token to the URL query params
            # The frontend (at /auth/callback) will parse this and set the
            # cookie/localStorage
            separator = '&' if '?' in url else '?'
            return f"{url}{separator}auth_token={token.key}"

        return url


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def get_login_redirect_url(self, request):
        # Social login also needs to pass the token
        url = super().get_login_redirect_url(request)

        if request.user.is_authenticated:
            token, created = Token.objects.get_or_create(user=request.user)
            separator = '&' if '?' in url else '?'
            return f"{url}{separator}auth_token={token.key}"

        return url
