"""Defense-in-depth test for Finding #122 (cookie presence vs validity).

The Next.js middleware at ``frontend/src/middleware.ts`` decides
whether to let a user past the auth wall by checking whether the
``auth_token`` cookie is *present*, not whether it is
cryptographically valid. A forged cookie value therefore passes
the middleware and the backend then rejects the request — which
is the safe outcome, but the middleware offers no defence in
depth.

This test asserts the current behaviour: the helper that
inspects the cookie (``hasAuthTokenCookie``) is a
truthiness-of-cookie-value check. It is *not* a signature
verification. Closing the gap is deferred to a future
hardening pass; the test is left in place to (a) make the gap
explicit and (b) fail loudly if a future change silently
removes the helper without adding cryptographic verification.
"""
from pathlib import Path

from django.test import SimpleTestCase


class Finding122CookiePresenceTests(SimpleTestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parents[4]
        self.middleware_path = repo_root / "frontend" / "src" / "middleware.ts"
        self.source = self.middleware_path.read_text(encoding="utf-8")

    def test_middleware_file_exists(self):
        self.assertTrue(self.middleware_path.exists())

    def test_presence_helper_is_defined(self):
        self.assertIn("hasAuthTokenCookie", self.source)
        self.assertIn("auth_token", self.source)

    def test_cookie_check_is_presence_only_no_crypto(self):
        """Defence-in-depth gap: no signature/JWT verification in
        the middleware. The cookie is treated as a sentinel; the
        backend performs the real authentication. Closing this gap
        (e.g. by adding a signed-cookie verification) is deferred
        to a future hardening pass — when that lands, this
        assertion must be updated to assert the new behaviour.
        """
        self.assertIn(".trim()", self.source)
        crypto_keywords = (
            "verify(", "jwt.decode", "jose", "jsonwebtoken",
            "crypto.createHmac", "hmac", "signature",
        )
        for kw in crypto_keywords:
            self.assertNotIn(
                kw,
                self.source,
                f"Middleware appears to perform crypto verification "
                f"({kw!r}); revisit the defence-in-depth gap test.",
            )
