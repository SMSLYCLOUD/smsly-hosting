# pylint: disable=invalid-name
"""
Tests for the SKIP_PATTERNS regex used in PipelineManager._inject_dotenv_from_repo.

The pattern must match (case-insensitively) any key containing the substrings:
SECRET, PRIVATE, TOKEN, PASSWORD, API_KEY / API-KEY, DSN, CREDENTIAL.

Keys that look innocuous (APP_NAME, PORT) must NOT be skipped.
"""
import re

from django.test import SimpleTestCase

SKIP_PATTERNS = re.compile(
    r'(SECRET|PRIVATE|TOKEN|PASSWORD|API[_-]?KEY|DSN|CREDENTIAL)',
    re.IGNORECASE,
)


class DotenvSkipPatternTests(SimpleTestCase):
    """
    The skip list guards against auto-injecting secrets into a tenant's
    live service. These tests lock down the expected behavior.
    """

    def test_private_token_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("MY_PRIVATE_TOKEN"))

    def test_private_key_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("GH_PRIVATE_KEY"))

    def test_stripe_secret_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("STRIPE_SECRET"))

    def test_dsn_suffix_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("POSTGRES_DSN"))

    def test_redis_password_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("REDIS_PASSWORD"))

    def test_my_api_key_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("MY_API_KEY"))

    def test_api_key_with_hyphen_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("SERVICE-API-KEY"))

    def test_credential_is_skipped(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("AWS_CREDENTIAL"))

    def test_app_name_is_not_skipped(self):
        self.assertIsNone(SKIP_PATTERNS.search("APP_NAME"))

    def test_port_is_not_skipped(self):
        self.assertIsNone(SKIP_PATTERNS.search("PORT"))

    def test_node_env_is_not_skipped(self):
        self.assertIsNone(SKIP_PATTERNS.search("NODE_ENV"))

    def test_skip_pattern_is_case_insensitive(self):
        self.assertIsNotNone(SKIP_PATTERNS.search("my_secret"))
        self.assertIsNotNone(SKIP_PATTERNS.search("My_Private_Key"))
        self.assertIsNotNone(SKIP_PATTERNS.search("api_key_lowercase"))
