# pylint: disable=invalid-name
"""Tests for the encrypted Cloudflare API token storage on PlatformConfig."""


from django.test import TestCase

from apps.deployments.models import PlatformConfig

VALID_TOKEN = "A" * 40  # exactly 40 chars, alphanumeric


class CloudflareTokenEncryptionTests(TestCase):
    """PlatformConfig.cloudflare_api_token must be encrypted at rest."""

    def setUp(self):
        self.cfg = PlatformConfig.load()

    def test_raw_db_value_is_not_plaintext(self):
        """After save, the field's raw DB value is NOT the plaintext token."""
        self.cfg.cloudflare_api_token = VALID_TOKEN
        self.cfg.save()

        # Use a raw cursor — `values_list()` goes through `from_db_value`
        # in modern Django and would transparently decrypt the ciphertext.
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT cloudflare_api_token FROM deployments_platformconfig WHERE id=%s",
                [self.cfg.pk],
            )
            raw = cur.fetchone()[0]

        self.assertIsNotNone(raw)
        self.assertNotEqual(raw, VALID_TOKEN)
        # Fernet ciphertexts start with 'gAAAAA' — confirm shape.
        self.assertTrue(
            raw.startswith("gAAAAA"),
            f"Expected Fernet ciphertext prefix, got: {raw[:20]!r}",
        )

    def test_model_accessor_returns_plaintext(self):
        """The model `.cloudflare_api_token` accessor returns the plaintext."""
        self.cfg.cloudflare_api_token = VALID_TOKEN
        self.cfg.save()

        fresh = PlatformConfig.load()
        self.assertEqual(fresh.cloudflare_api_token, VALID_TOKEN)

    def test_validation_rejects_short_token(self):
        """Tokens shorter than 40 chars must be rejected by validate_cloudflare_token."""
        self.cfg.cloudflare_api_token = "short-token"
        errors = self.cfg.validate_cloudflare_token()
        self.assertTrue(
            any("too short" in e for e in errors),
            f"Expected a 'too short' error, got {errors!r}",
        )

    def test_validation_rejects_invalid_charset(self):
        """Tokens with disallowed characters must be rejected."""
        self.cfg.cloudflare_api_token = "x" * 40 + "!"
        errors = self.cfg.validate_cloudflare_token()
        self.assertTrue(
            any("invalid characters" in e for e in errors),
            f"Expected a 'invalid characters' error, got {errors!r}",
        )

    def test_validation_accepts_empty_token(self):
        """Empty tokens must be allowed (operators may not configure Cloudflare)."""
        self.cfg.cloudflare_api_token = ""
        self.assertEqual(self.cfg.validate_cloudflare_token(), [])

    def test_validation_accepts_valid_token(self):
        """A 40+ char token of the right charset is accepted."""
        self.cfg.cloudflare_api_token = "abcd1234" + "-" + "X" * 40
        self.assertEqual(self.cfg.validate_cloudflare_token(), [])

    def test_clean_raises_on_invalid_token(self):
        """full_clean() must raise ValidationError for an invalid token."""
        self.cfg.cloudflare_api_token = "too-short"
        with self.assertRaises(Exception) as ctx:
            self.cfg.full_clean()
        msg = str(ctx.exception)
        self.assertIn("cloudflare_api_token", msg)

    def test_field_definition_uses_encrypted_char_field(self):
        """Sanity check: the field is declared as EncryptedCharField."""
        field = PlatformConfig._meta.get_field("cloudflare_api_token")
        from encrypted_model_fields.fields import EncryptedCharField
        self.assertIsInstance(field, EncryptedCharField)
        self.assertEqual(field.max_length, 512)
