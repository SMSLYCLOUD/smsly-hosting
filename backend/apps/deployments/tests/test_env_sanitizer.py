"""Tests for apps.deployments.utils.env_sanitizer.

The sanitizer is the single source of truth for stripping AI / template /
markdown leakage from environment variable values before they reach a
container's ``.env`` file.

Every leak we have seen in production must have a regression test here.
"""
from __future__ import annotations

from django.test import SimpleTestCase

from apps.deployments.utils.env_sanitizer import (
    sanitize_env_value,
    sanitize_for_env_file,
    is_placeholder,
    looks_wildcard_host,
)


class SanitizeBackticksTests(SimpleTestCase):
    def test_strips_balanced_backticks(self):
        # AI Senate once returned `` `admin@smsly.cloud` `` wrapped in
        # backticks because it confused env values with Traefik label syntax.
        self.assertEqual(
            sanitize_env_value("`admin@smsly.cloud`", key="ADMIN_EMAIL"),
            "admin@smsly.cloud",
        )

    def test_strips_balanced_smart_quotes(self):
        self.assertEqual(
            sanitize_env_value("\u201chello\u201d", key="FOO"),
            "hello",
        )

    def test_strips_balanced_single_quotes(self):
        self.assertEqual(
            sanitize_env_value("'foo'", key="FOO"),
            "foo",
        )

    def test_strips_balanced_double_quotes(self):
        self.assertEqual(
            sanitize_env_value('"foo"', key="FOO"),
            "foo",
        )

    def test_strips_nested_wrappers(self):
        # AI returned `` "`'value'" `` once.
        self.assertEqual(
            sanitize_env_value("\"`'value'`\"", key="FOO"),
            "value",
        )

    def test_strips_only_one_side_does_nothing(self):
        # Unbalanced — leave as-is (it's a real value, not wrapper noise).
        self.assertEqual(
            sanitize_env_value('"unclosed', key="FOO"),
            '"unclosed',
        )


class SanitizeTemplateWrappersTests(SimpleTestCase):
    def test_strips_double_brace_wrappers(self):
        self.assertEqual(
            sanitize_env_value("{{GENERATE}}", key="FOO"),
            "",
        )

    def test_strips_single_brace_wrappers(self):
        self.assertEqual(
            sanitize_env_value("{GENERATE}", key="FOO"),
            "",
        )

    def test_strips_angle_bracket_wrappers(self):
        # GATEWAY_IPS leaked as ``<list-of-trusted-gateway-IP/CIDR>``.
        self.assertEqual(
            sanitize_env_value("<list-of-trusted-gateway-IP/CIDR>", key="GATEWAY_IPS"),
            "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        )

    def test_keeps_angle_bracket_with_path(self):
        # If the value contains a slash inside, we don't strip — it might
        # be a URL with a path.
        self.assertEqual(
            sanitize_env_value("https://example.com/<foo>", key="FOO"),
            "https://example.com/<foo>",
        )

    def test_keeps_angle_bracket_with_space(self):
        # If the value contains a space, we don't strip — it's a sentence.
        self.assertEqual(
            sanitize_env_value("<change me please>", key="FOO"),
            "<change me please>",
        )


class SanitizeTrailingCommentTests(SimpleTestCase):
    def test_strips_trailing_js_comment(self):
        # RATE_LIMIT_CORS_DEV_ORIGINS once had a literal JS comment leak.
        self.assertEqual(
            sanitize_env_value(
                'https://example.com",  // production-safe default',
                key="RATE_LIMIT_CORS_DEV_ORIGINS",
            ),
            "https://example.com",
        )

    def test_strips_trailing_c_block_comment(self):
        self.assertEqual(
            sanitize_env_value(
                'value" /* foo */',
                key="FOO",
            ),
            "value",
        )

    def test_keeps_legitimate_hash_value(self):
        # ``#`` is NOT a comment marker in our regex — values can
        # legitimately contain ``#``.
        self.assertEqual(
            sanitize_env_value("password#with#hash", key="FOO"),
            "password#with#hash",
        )


class SanitizeNewlineTests(SimpleTestCase):
    def test_collapses_newlines(self):
        # Multi-line values would break the .env file format. Collapse.
        self.assertEqual(
            sanitize_env_value("line1\nline2", key="FOO"),
            "line1 line2",
        )

    def test_collapses_crlf(self):
        self.assertEqual(
            sanitize_env_value("line1\r\nline2", key="FOO"),
            "line1 line2",
        )

    def test_for_env_file_strips_leading_hash(self):
        # Leading ``#`` in a .env file is treated as a comment.
        self.assertEqual(
            sanitize_for_env_file("#hashed"),
            "\\#hashed",
        )


class SanitizeAllowedHostsTests(SimpleTestCase):
    def test_wildcard_replaced_with_safe_default(self):
        # ``*`` for ALLOWED_HOSTS is a security risk — force same-origin
        # only (empty string).
        self.assertEqual(
            sanitize_env_value("*", key="ALLOWED_HOSTS"),
            "",
        )

    def test_wildcard_for_django_allowed_hosts(self):
        self.assertEqual(
            sanitize_env_value("*", key="DJANGO_ALLOWED_HOSTS"),
            "",
        )

    def test_wildcard_for_cors(self):
        self.assertEqual(
            sanitize_env_value("*", key="CORS_ALLOWED_ORIGINS"),
            "",
        )

    def test_explicit_value_preserved(self):
        self.assertEqual(
            sanitize_env_value("app.example.com,api.example.com", key="ALLOWED_HOSTS"),
            "app.example.com,api.example.com",
        )

    def test_quoted_wildcard_normalized(self):
        # AI sometimes returns ``"*,"`` — strip the quote + comma and the
        # wildcard still triggers the safe-default substitution.
        self.assertEqual(
            sanitize_env_value('"*",', key="ALLOWED_HOSTS"),
            "",
        )


class SanitizePlaceholderTests(SimpleTestCase):
    def test_exact_generate(self):
        self.assertEqual(
            sanitize_env_value("GENERATE", key="FOO"),
            "",
        )

    def test_exact_changeme(self):
        self.assertEqual(
            sanitize_env_value("CHANGEME", key="FOO"),
            "",
        )

    def test_exact_todo(self):
        self.assertEqual(
            sanitize_env_value("TODO", key="FOO"),
            "",
        )

    def test_exact_your_api_key(self):
        self.assertEqual(
            sanitize_env_value("YOUR_API_KEY", key="FOO"),
            "",
        )

    def test_exact_your_token(self):
        self.assertEqual(
            sanitize_env_value("YOUR_TOKEN", key="FOO"),
            "",
        )

    def test_exact_na(self):
        self.assertEqual(
            sanitize_env_value("n/a", key="FOO"),
            "",
        )

    def test_exact_null(self):
        self.assertEqual(
            sanitize_env_value("null", key="FOO"),
            "",
        )

    def test_prefix_your(self):
        self.assertEqual(
            sanitize_env_value("YOUR_FOO_BAR", key="FOO"),
            "",
        )

    def test_prefix_replace_with(self):
        self.assertEqual(
            sanitize_env_value("REPLACE_WITH_VALUE", key="FOO"),
            "",
        )

    def test_empty(self):
        # Empty values are returned as "" by default, not None.
        self.assertEqual(sanitize_env_value("", key="FOO"), "")
        self.assertEqual(sanitize_env_value(None, key="FOO"), "")

    def test_empty_not_allowed_returns_none(self):
        # When the caller doesn't want to substitute a default, None is
        # returned and the caller is expected to drop the var.
        self.assertIsNone(sanitize_env_value("", key="FOO", allow_empty=False))
        self.assertIsNone(sanitize_env_value("GENERATE", key="FOO", allow_empty=False))


class SanitizeDefaultsByKeyTests(SimpleTestCase):
    def test_environment_default(self):
        self.assertEqual(
            sanitize_env_value("foo", key="ENVIRONMENT"),
            "foo",
        )
        # But the placeholder "TODO" should resolve to the default.
        self.assertEqual(
            sanitize_env_value("TODO", key="ENVIRONMENT"),
            "production",
        )

    def test_log_level_default(self):
        self.assertEqual(
            sanitize_env_value("CHANGEME", key="LOG_LEVEL"),
            "info",
        )

    def test_node_env_default(self):
        self.assertEqual(
            sanitize_env_value("CHANGEME", key="NODE_ENV"),
            "production",
        )

    def test_web_concurrency_default(self):
        self.assertEqual(
            sanitize_env_value("CHANGEME", key="WEB_CONCURRENCY"),
            "4",
        )

    def test_gateway_ips_default(self):
        self.assertEqual(
            sanitize_env_value("CHANGEME", key="GATEWAY_IPS"),
            "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        )


class SanitizeRealisticValuesTests(SimpleTestCase):
    def test_preserves_real_url(self):
        self.assertEqual(
            sanitize_env_value("https://api.example.com", key="API_URL"),
            "https://api.example.com",
        )

    def test_preserves_postgres_url(self):
        # DATABASE_URL contains a colon and slashes — must not be mangled.
        url = "postgresql://user:p@ss@db.internal:5432/app"
        self.assertEqual(
            sanitize_env_value(url, key="DATABASE_URL"),
            url,
        )

    def test_preserves_real_secret_value(self):
        # Real secret values look like hex/base64. We must not mangle them.
        self.assertEqual(
            sanitize_env_value("a1b2c3d4e5f6", key="MY_API_KEY"),
            "a1b2c3d4e5f6",
        )

    def test_preserves_real_email(self):
        self.assertEqual(
            sanitize_env_value("ops@example.com", key="ADMIN_EMAIL"),
            "ops@example.com",
        )

    def test_admin_email_with_backticks_stripped(self):
        # The exact leak from the ecosystem deploy: `` `admin@smsly.cloud` ``
        self.assertEqual(
            sanitize_env_value("`admin@smsly.cloud`", key="ADMIN_EMAIL"),
            "admin@smsly.cloud",
        )

    def test_admin_email_quoted_stripped(self):
        # ``"admin@smsly.cloud"``
        self.assertEqual(
            sanitize_env_value('"admin@smsly.cloud"', key="ADMIN_EMAIL"),
            "admin@smsly.cloud",
        )


class IsPlaceholderTests(SimpleTestCase):
    def test_real_value_not_placeholder(self):
        self.assertFalse(is_placeholder("hello"))

    def test_empty_is_placeholder(self):
        self.assertTrue(is_placeholder(""))
        self.assertTrue(is_placeholder("   "))
        self.assertTrue(is_placeholder(None))

    def test_generate_is_placeholder(self):
        self.assertTrue(is_placeholder("GENERATE"))
        self.assertTrue(is_placeholder("{GENERATE}"))
        self.assertTrue(is_placeholder("{{GENERATE}}"))

    def test_changeme_is_placeholder(self):
        self.assertTrue(is_placeholder("CHANGEME"))
        self.assertTrue(is_placeholder("changeme"))

    def test_your_prefix_is_placeholder(self):
        self.assertTrue(is_placeholder("YOUR_API_KEY"))
        self.assertTrue(is_placeholder("YOUR_FOO"))

    def test_replace_with_prefix_is_placeholder(self):
        self.assertTrue(is_placeholder("REPLACE_WITH_FOO"))

    def test_angle_bracket_simple_is_placeholder(self):
        # ``<FOO>`` with no spaces or slashes inside is a placeholder
        # marker. ``<https://example.com>`` is NOT because of the slashes.
        self.assertTrue(is_placeholder("<FOO>"))
        self.assertFalse(is_placeholder("<https://example.com>"))


class WildcardTests(SimpleTestCase):
    def test_asterisk(self):
        self.assertTrue(looks_wildcard_host("*"))

    def test_bare_asterisk(self):
        self.assertFalse(looks_wildcard_host(""))
        self.assertFalse(looks_wildcard_host(None))

    def test_asterisk_comma_asterisk(self):
        # ``*,*`` is the bogus "allow all" pattern some users paste.
        self.assertTrue(looks_wildcard_host("*,*"))

    def test_explicit_host_not_wildcard(self):
        self.assertFalse(looks_wildcard_host("api.example.com"))
        self.assertFalse(looks_wildcard_host("*.example.com"))  # subdomain wildcard is a different question
