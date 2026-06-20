"""
HttpOnly auth cookie helpers.

The auth token is delivered to the browser as an HttpOnly+Secure+SameSite=Strict
cookie. The cookie is set by the login view and cleared by the logout view.

Cookie name selection:
- In production (non-DEBUG, served over HTTPS) we use the ``__Host-auth_token``
  prefix. The ``__Host-`` prefix instructs the browser to (a) reject the cookie
  unless the request is secure, (b) reject it unless ``Secure`` is set,
  (c) reject it unless ``Path=/`` and no ``Domain`` attribute is present.
  These constraints protect against subdomain cookie injection attacks
  (e.g. an attacker-controlled ``*.example.com`` cannot clobber the auth
  cookie by setting a same-name cookie on a less-trusted subdomain).
- In development (DEBUG=True) the page may be served over plain HTTP, so the
  ``__Host-`` prefix would be rejected by the browser. We fall back to a
  plain ``auth_token`` name (still HttpOnly, still SameSite=Strict, but no
  ``Secure`` flag).

This module is also consumed by DRF's auth class
(``apps.core.auth.CookieAwareTokenAuthentication``) and the security
middleware (``apps.core.middleware.security.SecurityMiddleware``) which
treats the cookie as a valid auth credential and skips HMAC enforcement.
"""

from django.conf import settings
from django.http import HttpResponse

# 30 days — matches the lifetime the rest of the platform uses for long-lived
# auth tokens.
AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30

# Plain name for development (no __Host- prefix because it would be rejected
# on plain HTTP).
DEV_COOKIE_NAME = "auth_token"

# Hardened name for production (HTTPS only). The __Host- prefix REQUIRES:
#   * Secure attribute set
#   * Path=/
#   * No Domain attribute
# Browsers will refuse to set the cookie if any of these are violated, so the
# helpers below must be careful to honour all three.
PROD_COOKIE_NAME = "__Host-auth_token"


def cookie_name() -> str:
    """Return the cookie name appropriate for the current environment.

    The decision is made per-request because ``settings.DEBUG`` is read at
    runtime (the test-suite flips DEBUG via ``override_settings``).
    """
    if getattr(settings, "DEBUG", False):
        return DEV_COOKIE_NAME
    return PROD_COOKIE_NAME


def set_auth_cookie(response: HttpResponse, token: str) -> None:
    """Attach the auth token as an HttpOnly+SameSite=Strict cookie.

    The cookie name, ``Secure`` flag, and lifetime are derived from the
    current environment (see module docstring).

    Args:
        response: The HttpResponse that will be returned to the client.
            The Set-Cookie header is added in-place.
        token: The opaque auth token to embed in the cookie.
    """
    is_secure = not getattr(settings, "DEBUG", False) and getattr(settings, "USE_SSL", False)
    name = cookie_name()
    # The __Host- prefix requires Secure=True, path=/, and no Domain. Plain
    # cookies do not need Secure, but we still set it when serving over HTTPS
    # so the browser refuses to send the cookie over a plaintext connection.
    response.set_cookie(
        key=name,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
        secure=is_secure,
        httponly=True,
        samesite="Strict",
        path="/",
    )


def delete_auth_cookie(response: HttpResponse) -> None:
    """Delete the auth cookie (both prod and dev names).

    We delete both names so a deployment that flipped DEBUG=True/False between
    login and logout still cleans up correctly.
    """
    response.delete_cookie(DEV_COOKIE_NAME, path="/")
    response.delete_cookie(PROD_COOKIE_NAME, path="/")


def get_cookie_token(request) -> str | None:
    """Read the auth token from the HttpOnly cookie on the request.

    Returns the production cookie value first (if present), then falls back
    to the development cookie. The browser sends the cookie automatically
    because the response that set it included ``credentials: 'include'`` /
    ``withCredentials: true`` on the frontend.
    """
    cookies = getattr(request, "COOKIES", None) or {}
    return cookies.get(PROD_COOKIE_NAME) or cookies.get(DEV_COOKIE_NAME)
