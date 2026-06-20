import contextlib
import os
import tempfile

from django.test import SimpleTestCase

from apps.deployments.services.transfer_service import (
    _TRANSFER_SCRUB_KEYS,
    ServerTransferService,
    _scrub_env_for_transfer,
)


class FieldEncryptionKeyScrubSetTests(SimpleTestCase):
    """FIELD_ENCRYPTION_KEY must NOT be in _TRANSFER_SCRUB_KEYS.

    The FULL transfer ships a scrubbed .env to the target. The
    target also receives db_dump.sql containing rows encrypted
    with the source's FIELD_ENCRYPTION_KEY. If the scrubber
    removed FIELD_ENCRYPTION_KEY from the shipped .env, the
    target would regenerate a new key, and EncryptedCharField
    would silently decrypt every encrypted column to "" (the
    Batch J safe_to_python patch swallows the InvalidToken).
    """

    def test_field_encryption_key_not_in_scrub_set(self):
        self.assertNotIn("FIELD_ENCRYPTION_KEY", _TRANSFER_SCRUB_KEYS)

    def test_scrub_set_keeps_other_platform_secrets(self):
        self.assertIn("BACKUP_ENCRYPTION_KEY", _TRANSFER_SCRUB_KEYS)
        self.assertIn("GATEWAY_SECRET", _TRANSFER_SCRUB_KEYS)
        self.assertIn("CLOUDFLARE_API_TOKEN", _TRANSFER_SCRUB_KEYS)

    def test_field_encryption_key_value_passes_through_unchanged(self):
        """The actual VALUE of FIELD_ENCRYPTION_KEY must reach the
        target unmodified. Without it, every EncryptedCharField
        row in db_dump.sql would silently decrypt to "" on the
        target.
        """
        env = (
            "FIELD_ENCRYPTION_KEY=AABBccdd==\n"
            "POSTGRES_USER=app\n"
        )
        fd, path = tempfile.mkstemp(suffix=".env")
        try:
            os.write(fd, env.encode())
            os.close(fd)
            scrubbed = _scrub_env_for_transfer(path)
        finally:
            os.unlink(path)
        self.assertIn("FIELD_ENCRYPTION_KEY=AABBccdd==", scrubbed)
        scrubbed_lines = [
            line for line in scrubbed.splitlines()
            if "FIELD_ENCRYPTION_KEY" in line
        ]
        self.assertEqual(
            len(scrubbed_lines), 1,
            f"Expected exactly one FIELD_ENCRYPTION_KEY line, got: {scrubbed_lines}",
        )
        self.assertIn("POSTGRES_USER=app", scrubbed)

    def test_other_secrets_still_get_scrubbed(self):
        """Sanity: while FIELD_ENCRYPTION_KEY is no longer scrubbed,
        the other platform secrets (BACKUP_ENCRYPTION_KEY,
        GATEWAY_SECRET) are still stripped.
        """
        env = (
            "FIELD_ENCRYPTION_KEY=keep-me\n"
            "BACKUP_ENCRYPTION_KEY=scrub-me-1\n"
            "GATEWAY_SECRET=scrub-me-2\n"
        )
        fd, path = tempfile.mkstemp(suffix=".env")
        try:
            os.write(fd, env.encode())
            os.close(fd)
            scrubbed = _scrub_env_for_transfer(path)
        finally:
            os.unlink(path)
        self.assertIn("FIELD_ENCRYPTION_KEY=keep-me", scrubbed)
        self.assertNotIn("scrub-me-1", scrubbed)
        self.assertNotIn("scrub-me-2", scrubbed)
        self.assertIn(
            "# BACKUP_ENCRYPTION_KEY=<OPERATOR-MUST-SET-AFTER-TRANSFER>",
            scrubbed,
        )
        self.assertIn(
            "# GATEWAY_SECRET=<OPERATOR-MUST-SET-AFTER-TRANSFER>",
            scrubbed,
        )


class FieldEncryptionKeyShipmentWarningTests(SimpleTestCase):
    """The FULL transfer must surface a WARNING log line so the
    operator knows FIELD_ENCRYPTION_KEY is being shipped.

    This is a behavioural test: when _upload runs the FULL branch
    with a real .env containing FIELD_ENCRYPTION_KEY, a WARNING
    line about the key being shipped is appended to the transfer
    log. We verify this without a real SSH by mocking _scrub_env
    and ssh.upload_file so the call chain runs to completion.
    """

    def test_warning_logged_before_shipping_env(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        transfer = SimpleNamespace(
            id="test-transfer",
            transfer_type="FULL",
            target_server_ip="10.0.0.99",
            source_server_ip="10.0.0.1",
            source_backup=None,
            source_server_backup=None,
            logs="",
        )
        svc = ServerTransferService(transfer)
        svc.transfer = transfer
        svc._update = lambda *a, **kw: None

        backup = SimpleNamespace(file_path="/tmp/does-not-exist.tar.gz")
        transfer.source_backup = backup

        log_calls = []
        svc._log = log_calls.append

        ssh = MagicMock()
        ssh.upload_file = MagicMock()
        ssh.exec_command = MagicMock(return_value=("", "", 0))
        ssh.find_hosting_path = MagicMock(return_value="/opt/smsly-hosting")
        svc.ssh = ssh

        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': ''}, clear=False):
            with contextlib.suppress(Exception):
                svc._upload()

        joined = "\n".join(log_calls)
        self.assertIn("WARNING", joined)
        self.assertIn("FIELD_ENCRYPTION_KEY", joined)
        self.assertIn("shipping", joined.lower())
