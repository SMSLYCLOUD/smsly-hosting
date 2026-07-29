"""Frontend compatibility alias: /api/v1/accounts/

The frontend login page (login/page.tsx:115, 121, 127, 133)
uses OAuth link hrefs like ``/api/v1/accounts/github/login/``,
``/api/v1/accounts/google/login/``,
``/api/v1/accounts/gitlab/login/``, and
``/api/v1/accounts/bitbucket_oauth2/login/``. The allauth
URLs are mounted at ``/accounts/...`` (no /api/v1/ prefix).
This alias re-exports the same allauth.urls under the
``/api/v1/accounts/`` path so the OAuth start links resolve.
"""
from django.urls import include, path

urlpatterns = [
    path('', include('allauth.urls')),
]
