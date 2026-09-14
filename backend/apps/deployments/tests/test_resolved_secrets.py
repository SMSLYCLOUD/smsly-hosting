"""Unit tests for the deploy-time placeholder-secret gate (no DB)."""
from unittest import TestCase

from apps.deployments.services.manifest_env_resolver.secrets import (
    validate_resolved_secrets,
)


class TestValidateResolvedSecrets(TestCase):
    def test_clean_env_passes(self):
        env = {
            "ENVIRONMENT": "production",
            "SDK_HEADER_VALUE": "a" * 48,
            "DJANGO_SECRET_KEY": "x" * 50,
            "DEBUG": "false",
        }
        self.assertEqual(validate_resolved_secrets(env), [])

    def test_gateway_crash_case(self):
        """2026-09-14 live incident: CHANGE_ME shipped to production."""
        env = {"ENVIRONMENT": "production", "SDK_HEADER_VALUE": "CHANGE_ME"}
        problems = validate_resolved_secrets(env)
        self.assertEqual(len(problems), 1)
        self.assertIn("SDK_HEADER_VALUE", problems[0])

    def test_empty_critical_secret_fails(self):
        problems = validate_resolved_secrets(
            {"ENVIRONMENT": "production", "SDK_HEADER_VALUE": ""}
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("SDK_HEADER_VALUE", problems[0])

    def test_short_critical_secret_fails_in_prod(self):
        problems = validate_resolved_secrets(
            {"ENVIRONMENT": "production", "SDK_HEADER_VALUE": "short"}
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("too short", problems[0])

    def test_short_critical_secret_allowed_in_dev(self):
        problems = validate_resolved_secrets(
            {"ENVIRONMENT": "development", "SDK_HEADER_VALUE": "short"}
        )
        self.assertEqual(problems, [])

    def test_placeholder_still_rejected_in_dev(self):
        problems = validate_resolved_secrets(
            {"ENVIRONMENT": "dev", "SDK_HEADER_VALUE": "CHANGE_ME"}
        )
        self.assertEqual(len(problems), 1)

    def test_missing_key_is_not_a_problem(self):
        """The gate only judges keys the app actually declares."""
        self.assertEqual(validate_resolved_secrets({"ENVIRONMENT": "production"}), [])

    def test_placeholder_variants_normalized(self):
        for variant in ("change_me", "Change-Me", "  REPLACE_ME  ", "changeme"):
            problems = validate_resolved_secrets(
                {"DJANGO_SECRET_KEY": variant}
            )
            self.assertEqual(len(problems), 1, variant)

    def test_mock_values_are_not_placeholders(self):
        env = {
            "TWILIO_SID": "AC" + "0" * 32,
            "STRIPE_KEY": "sk_test_mock_12345678",
            "EMAIL_HOST_USER": "mock@localhost",
        }
        self.assertEqual(validate_resolved_secrets(env), [])

    def test_empty_env_passes(self):
        self.assertEqual(validate_resolved_secrets({}), [])
