"""Unit tests for build-time secret classification (no DB)."""
from unittest import TestCase

from apps.cloud.services.build_constants import is_secret_env_var


class TestIsSecretEnvVar(TestCase):
    def test_sdk_header_value_is_secret(self):
        """2026-09-14 audit: SDK_HEADER_VALUE authenticates SDK callers but
        matched no suffix rule, so it was sent to the AI Senate in cleartext,
        withheld from nothing, and shown unmasked."""
        self.assertTrue(is_secret_env_var("SDK_HEADER_VALUE"))

    def test_known_secrets_still_match(self):
        for name in ("DATABASE_URL", "JWT_SECRET", "REDIS_URL", "API_KEY",
                     "DJANGO_SECRET_KEY", "CELERY_BROKER_URL", "MY_PASSWORD"):
            self.assertTrue(is_secret_env_var(name), name)

    def test_public_vars_stay_public(self):
        for name in ("NEXT_PUBLIC_API_URL", "VITE_API_URL", "PUBLIC_KEY_PATH",
                     "TOKEN_TYPE", "DEBUG", "ENVIRONMENT", "PORT"):
            self.assertFalse(is_secret_env_var(name), name)
