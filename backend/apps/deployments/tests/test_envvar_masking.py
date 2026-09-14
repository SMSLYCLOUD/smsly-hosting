"""Unit tests for env var value masking in API responses (no DB)."""
from types import SimpleNamespace
from unittest import TestCase

from apps.deployments.serializers.service import (
    EnvVarSerializer,
    _must_mask_env_value,
)


def _row(key, value, is_secret=False):
    return SimpleNamespace(
        id=1, key=key, value=value, is_secret=is_secret,
        is_locked=False, source="USER", service_id="svc-1",
    )


def _render(row, reveal=False):
    return EnvVarSerializer(row, context={"reveal_secrets": reveal}).data


class TestMustMaskEnvValue(TestCase):
    def test_flagged_rows_masked(self):
        self.assertTrue(_must_mask_env_value("ANYTHING", "plain", True))

    def test_sdk_header_value_masked_by_name(self):
        self.assertTrue(_must_mask_env_value("SDK_HEADER_VALUE", "x" * 48, False))

    def test_credentialed_urls_masked(self):
        self.assertTrue(_must_mask_env_value(
            "PG_URL", "postgresql://u:p@host:5432/db", False))
        self.assertTrue(_must_mask_env_value(
            "REDIS_URI", "redis://:pass@host:6379/0", False))
        self.assertTrue(_must_mask_env_value(
            "CELERY_BROKER_URL", "redis://:pass@host:6379/0", False))

    def test_plain_urls_not_masked(self):
        self.assertFalse(_must_mask_env_value(
            "AUDIT_SERVICE_URL", "https://audit:80", False))
        self.assertFalse(_must_mask_env_value("PORT", "8080", False))
        self.assertFalse(_must_mask_env_value("DEBUG", "false", False))


class TestEnvVarSerializerMasking(TestCase):
    def test_masks_stale_unflagged_secret(self):
        out = _render(_row("POSTGRESQL_URL", "postgresql://u:p@h:5432/d"))
        self.assertEqual(out["value"], "********")

    def test_masks_sdk_header_value(self):
        out = _render(_row("SDK_HEADER_VALUE", "y" * 48))
        self.assertEqual(out["value"], "********")

    def test_leaves_public_values(self):
        out = _render(_row("LOG_LEVEL", "INFO"))
        self.assertEqual(out["value"], "INFO")

    def test_reveal_context_shows_value(self):
        out = _render(_row("PG_URL", "postgresql://u:p@h:5432/d"), reveal=True)
        self.assertEqual(out["value"], "postgresql://u:p@h:5432/d")
