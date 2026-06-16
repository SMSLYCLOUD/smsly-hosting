from pathlib import Path

from django.test import SimpleTestCase


class Finding122DualCookieCheckTests(SimpleTestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parents[4]
        self.middleware_path = (
            repo_root / "frontend" / "src" / "middleware.ts"
        )
        self.source = self.middleware_path.read_text(encoding="utf-8")

    def test_middleware_file_exists(self):
        self.assertTrue(self.middleware_path.exists())

    def test_csrf_token_cookie_helper_is_defined(self):
        self.assertIn("hasCsrfTokenCookie", self.source)
        self.assertIn("csrf_token", self.source)

    def test_dual_cookie_redirect_pattern_present(self):
        import re
        pattern = re.compile(
            r"hasApiToken\s*&&\s*!hasCsrf[\s\S]{0,200}"
            r"redirect\([^)]*login",
            re.MULTILINE,
        )
        self.assertRegex(
            self.source,
            pattern.pattern,
            "Middleware must redirect to /login when auth_token is "
            "present without csrf_token on a protected path.",
        )
