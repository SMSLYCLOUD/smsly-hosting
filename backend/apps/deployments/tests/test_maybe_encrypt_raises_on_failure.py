"""
Tests for ``_maybe_encrypt`` raising on encryption failure.

When a ``BACKUP_ENCRYPTION_KEY`` is provided but the encryption
process itself fails (e.g. disk error, corrupted key material),
the method *no longer falls back* to retaining the cleartext
archive — it deletes the source file and raises
:class:`BackupEncryptionRequired`.

The old behaviour was two-pronged: if
``BACKUP_REQUIRE_ENCRYPTION`` was set, it would raise; otherwise
it would log a warning and return the original cleartext path.
The new behaviour is unconditional: if a key was explicitly
provided and encryption fails — for any reason — the original
is destroyed and the error propagates.
"""
import os
import tarfile
import tempfile
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.services.backup_service import (
    BackupEncryptionRequired,
    BackupService,
)

User = get_user_model()


class MaybeEncryptRaisesOnFailureTest(TestCase):
    """When a key *is* set but encryption fails, raise — don't fall back."""

    def setUp(self):
        self.user = User.objects.create_user(username="fail-tester", password="x")
        self.project = Project.objects.create(name="fail-proj", owner=self.user)
        self.service = Service.objects.create(
            name="fail-svc",
            owner=self.user,
            project=self.project,
        )

    @staticmethod
    def _write_fake_archive() -> str:
        """Create a small tar.gz and return its path."""
        tmp = tempfile.mkdtemp(prefix="enc-fail-")
        archive = os.path.join(tmp, "fake.tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name="data.txt")
            data = b"encryption-failure-test-data"
            info.size = len(data)
            info.type = tarfile.REGTYPE
            tar.addfile(info, __import__("io").BytesIO(data))
        return archive

    def test_encryption_failure_deletes_original_and_raises(self):
        """Given a valid key + an AESGCM that bombs, the original archive
        is removed and BackupEncryptionRequired is raised."""
        key = Fernet.generate_key().decode()
        archive = self._write_fake_archive()
        self.assertTrue(os.path.exists(archive))

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        with patch.object(AESGCM, "encrypt", side_effect=RuntimeError("disk full")):
            with patch.dict(
                os.environ,
                {"BACKUP_ENCRYPTION_KEY": key},
                clear=False,
            ):
                with self.assertRaises(BackupEncryptionRequired) as ctx:
                    BackupService()._maybe_encrypt(archive)

        # The original must NOT exist anymore — no cleartext fallback.
        self.assertFalse(
            os.path.exists(archive),
            "Original archive should have been removed on encryption failure",
        )
        self.assertIn("Encryption failed", str(ctx.exception))

    def test_encryption_failure_without_require_encryption_flag_still_raises(self):
        """Even when BACKUP_REQUIRE_ENCRYPTION is explicitly off, if a key
        is provided and encryption fails, we still raise — no fallback."""
        key = Fernet.generate_key().decode()
        archive = self._write_fake_archive()

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        with patch.object(AESGCM, "encrypt", side_effect=OSError("write error")):
            with patch.dict(
                os.environ,
                {
                    "BACKUP_ENCRYPTION_KEY": key,
                    "BACKUP_REQUIRE_ENCRYPTION": "false",
                },
                clear=False,
            ):
                with self.assertRaises(BackupEncryptionRequired):
                    BackupService()._maybe_encrypt(archive)

        self.assertFalse(os.path.exists(archive))

    def test_no_key_still_allows_cleartext_when_not_required(self):
        """No key + requirement off = still returns cleartext (no regression)."""
        archive = self._write_fake_archive()
        with patch.dict(
            os.environ,
            {"BACKUP_ENCRYPTION_KEY": "", "BACKUP_REQUIRE_ENCRYPTION": "false"},
            clear=False,
        ):
            result = BackupService()._maybe_encrypt(archive)
        self.assertEqual(result, archive)
        self.assertTrue(os.path.exists(archive))

    def test_no_key_raises_when_encryption_is_required(self):
        """No key + requirement on = raises (no regression)."""
        archive = self._write_fake_archive()
        with patch.dict(
            os.environ,
            {"BACKUP_ENCRYPTION_KEY": "", "BACKUP_REQUIRE_ENCRYPTION": "true"},
            clear=False,
        ):
            with self.assertRaises(BackupEncryptionRequired):
                BackupService()._maybe_encrypt(archive)
        # Original should remain since no encryption was attempted
        self.assertTrue(os.path.exists(archive))
