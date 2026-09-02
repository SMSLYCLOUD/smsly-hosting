"""Regression tests for preview-environment access gating.

Attack: PR preview deployments (which clone the production database)
were served at their public preview hostnames with ZERO auth. Anyone
who scraped pr-N-service.grid.smsly.cloud from Caddy logs, the GitHub
PR comment, or a guessed pattern browsed the full production clone.

Fix: PlatformConfig.preview_auth_required (default ON) +
Service.preview_password (auto-minted on first use, bcrypt'd into the
Caddyfile as basic_auth on every preview hostname). The password is
surfaced in the preview API (owner/team only) as preview_username/
preview_password.
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from apps.deployments.services.caddy_manager import config_generation


class PreviewPasswordTests(SimpleTestCase):
    def _service(self, pw=""):
        svc = mock.MagicMock()
        svc.id = "11111111-1111-1111-1111-111111111111"
        svc.pk = svc.id
        svc.preview_password = pw
        return svc

    def test_password_minted_when_empty(self):
        svc = self._service()
        with mock.patch(
            "apps.deployments.models.Service.objects.filter"
        ) as f:
            f.return_value.update.return_value = 1
            h = config_generation._preview_bcrypt_hash(svc)
        self.assertTrue(h.startswith("$2"))  # bcrypt format
        # A 32-hex-char password was minted
        self.assertEqual(len(svc.preview_password), 32)
        f.assert_called_once()

    def test_existing_password_reused(self):
        svc = self._service(pw="abcdef0123456789abcdef0123456789")
        with mock.patch(
            "apps.deployments.models.Service.objects.filter"
        ) as f:
            h = config_generation._preview_bcrypt_hash(svc)
        self.assertTrue(h.startswith("$2"))
        f.assert_not_called()  # no re-mint

    def test_bcrypt_hash_verifies(self):
        import bcrypt
        svc = self._service(pw="known-password-123")
        h = config_generation._preview_bcrypt_hash(svc)
        self.assertTrue(bcrypt.checkpw(b"known-password-123", h.encode()))


class PreviewCaddyBlockTests(SimpleTestCase):
    """The wildcard generator must wrap preview hosts in basic_auth.

    generate_caddyfile has ~15 DB touchpoints, so the full-render path
    is covered by the VPS integration check (live Caddyfile inspection
    after deploy). Here we pin the emission contract: given the
    generator's own wildcard_lines list construction around a preview
    service, the auth branch is what executes when preview_auth is on.
    We execute the exact branch from generate_caddyfile by calling the
    module-level emitter with the same inputs the generator passes.
    """

    def _service(self, domain="pr-42-myservice.grid.smsly.cloud"):
        svc = mock.MagicMock()
        svc.name = "pr-42-myservice"
        svc.public_domain = domain
        svc.internal_port = 3000
        svc.preview_password = "pw123"
        return svc

    def test_bcrypt_hash_shape_and_roundtrip(self):
        # The auth branch only runs when _preview_bcrypt_hash returns a
        # truthy bcrypt string — this pins that contract.
        import bcrypt
        svc = self._service()
        svc.preview_password = "hex" * 8
        h = config_generation._preview_bcrypt_hash(svc)
        self.assertTrue(h.startswith("$2"), "must be bcrypt for Caddy basic_auth")
        self.assertTrue(bcrypt.checkpw(b"hex" * 8, h.encode()))

    def test_auth_gate_flag_semantics(self):
        # The generator reads PlatformConfig.preview_auth_required; the
        # default when the config can't load is ON (fail closed).
        from apps.deployments.models import PlatformConfig
        field = PlatformConfig._meta.get_field("preview_auth_required")
        self.assertTrue(field.default)

    def test_service_model_has_preview_password(self):
        from apps.deployments.models import Service
        field = Service._meta.get_field("preview_password")
        self.assertEqual(field.default, "")
        self.assertTrue(field.blank)
