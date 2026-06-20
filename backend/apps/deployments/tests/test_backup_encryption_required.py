# pylint: disable=invalid-name
"""
Tests for mandatory backup encryption.

When ``BACKUP_REQUIRE_ENCRYPTION`` is set and ``BACKUP_ENCRYPTION_KEY`` is
missing, the backup service must refuse to write the backup in cleartext
and raise :class:`BackupEncryptionRequired`.
"""

import contextlib
import os
import tempfile
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()


class BackupEncryptionRequiredTests(TestCase):
    """Refuse to write backups unencrypted when BACKUP_REQUIRE_ENCRYPTION is set."""

    def setUp(self):
        from apps.deployments.models import Project, Service
        from apps.deployments.services.backup_service import (
            BackupEncryptionRequired,
            BackupService,
        )

        self.BackupEncryptionRequired = BackupEncryptionRequired
        self.BackupService = BackupService
        self.user = User.objects.create_user(username="enc-tester", password="x")
        self.project = Project.objects.create(name="enc", owner=self.user)
        self.service = Service.objects.create(
            name="enc-svc",
            owner=self.user,
            project=self.project,
        )

    def _write_fake_archive(self) -> str:
        """Create a small tar.gz in a temp dir and return its path."""
        import tarfile

        tmp = tempfile.mkdtemp(prefix="enc-test-")
        archive = os.path.join(tmp, "fake.tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name="hello.txt")
            data = b"hello world"
            info.size = len(data)
            tar.addfile(info, __import__("io").BytesIO(data))
        return archive

    @patch.dict(os.environ, {}, clear=False)
    def test_backup_require_encryption_true_and_no_key_raises(self):
        # Force the env override (in case settings.py would not be set in tests).
        with patch.dict(
            os.environ,
            {"BACKUP_REQUIRE_ENCRYPTION": "true", "BACKUP_ENCRYPTION_KEY": ""},
            clear=False,
        ):
            archive = self._write_fake_archive()
            try:
                with self.assertRaises(self.BackupEncryptionRequired):
                    self.BackupService()._maybe_encrypt(archive)
            finally:
                for path in (archive,):
                    if os.path.exists(path):
                        os.remove(path)
                    parent = os.path.dirname(path)
                    if os.path.isdir(parent):
                        with contextlib.suppress(OSError):
                            os.rmdir(parent)

    @patch.dict(os.environ, {}, clear=False)
    def test_backup_require_encryption_via_settings_true_and_no_key_raises(self):
        with patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": ""}, clear=False):
            with override_settings(BACKUP_REQUIRE_ENCRYPTION=True):
                archive = self._write_fake_archive()
                try:
                    with self.assertRaises(self.BackupEncryptionRequired):
                        self.BackupService()._maybe_encrypt(archive)
                finally:
                    for path in (archive,):
                        if os.path.exists(path):
                            os.remove(path)
                        parent = os.path.dirname(path)
                        if os.path.isdir(parent):
                            with contextlib.suppress(OSError):
                                os.rmdir(parent)

    @patch.dict(os.environ, {}, clear=False)
    def test_backup_with_key_succeeds_and_is_encrypted(self):
        key = Fernet.generate_key().decode()
        with patch.dict(
            os.environ,
            {
                "BACKUP_REQUIRE_ENCRYPTION": "true",
                "BACKUP_ENCRYPTION_KEY": key,
            },
            clear=False,
        ):
            archive = self._write_fake_archive()
            original_bytes = open(archive, "rb").read()
            try:
                enc_path = self.BackupService()._maybe_encrypt(archive)
                self.assertTrue(enc_path.endswith(".enc"))
                self.assertTrue(os.path.exists(enc_path))
                self.assertFalse(os.path.exists(archive))
                # Round-trip decrypt
                dec_path = self.BackupService.decrypt_backup(enc_path, key)
                try:
                    with open(dec_path, "rb") as f:
                        self.assertEqual(f.read(), original_bytes)
                finally:
                    if os.path.exists(dec_path):
                        os.remove(dec_path)
            finally:
                for path in (archive,):
                    if os.path.exists(path):
                        os.remove(path)
                    parent = os.path.dirname(path)
                    if os.path.isdir(parent):
                        with contextlib.suppress(OSError):
                            os.rmdir(parent)

    @patch.dict(os.environ, {}, clear=False)
    def test_backup_require_encryption_false_silently_allows_missing_key(self):
        # Existing behavior: missing key + BACKUP_REQUIRE_ENCRYPTION off
        # is allowed (returns the original path).
        with patch.dict(
            os.environ,
            {"BACKUP_REQUIRE_ENCRYPTION": "false", "BACKUP_ENCRYPTION_KEY": ""},
            clear=False,
        ), override_settings(BACKUP_REQUIRE_ENCRYPTION=False):
            archive = self._write_fake_archive()
            try:
                result = self.BackupService()._maybe_encrypt(archive)
                self.assertEqual(result, archive)
                self.assertTrue(os.path.exists(archive))
            finally:
                for path in (archive,):
                    if os.path.exists(path):
                        os.remove(path)
                    parent = os.path.dirname(path)
                    if os.path.isdir(parent):
                        with contextlib.suppress(OSError):
                            os.rmdir(parent)
