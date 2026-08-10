"""Tests that decrypted backup files use a private dir + 0o600 mode + cleanup."""
import os
import platform
import stat
import tempfile
from unittest import skipIf
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.deployments.models.backup import ServerBackup, ServiceBackup
from apps.deployments.services.backup_service import BackupService

IS_WINDOWS = platform.system() == 'Windows'
User = get_user_model()


def _make_signed(pk: str) -> str:
    return signing.TimestampSigner().sign_object({'pk': str(pk), 'ts': 0})


def _encrypt_chunked(source: bytes, key: str) -> bytes:
    """Replicate the chunked AES-GCM encryption format for tests."""
    import hashlib
    import struct

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from apps.deployments.services.backup_service import (
        _CHUNKED_BACKUP_FINGERPRINT_BYTES,
        _CHUNKED_BACKUP_KEY_ID_BYTES,
        _CHUNKED_BACKUP_MAGIC,
        _CHUNKED_BACKUP_NONCE_PREFIX_BYTES,
    )

    raw_key = BackupService._decode_backup_key(key)
    aesgcm = AESGCM(raw_key)
    nonce_prefix = os.urandom(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
    out = bytearray()
    out += _CHUNKED_BACKUP_MAGIC
    out += nonce_prefix
    key_id_raw = struct.pack('>I', int(hashlib.md5(key.encode()).hexdigest()[:8], 16))
    out += key_id_raw
    fp_raw = struct.pack('>I', int(hashlib.md5(key.encode()).hexdigest()[:8], 16))
    out += fp_raw
    chunk_size = 64 * 1024
    for start in range(0, len(source), chunk_size):
        chunk = source[start:start + chunk_size]
        nonce_suffix = os.urandom(12)
        nonce = nonce_prefix + nonce_suffix
        ciphertext = aesgcm.encrypt(nonce, chunk, None)
        chunk_data = nonce_suffix + ciphertext
        out += struct.pack(">I", len(chunk_data))
        out += chunk_data
    out += struct.pack(">I", 0)
    return bytes(out)


class DecryptedBackupCleanupTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='dec-owner', password='x',
        )
        self.project = Project.objects.create(name='P', owner=self.owner)
        self.service = Service.objects.create(
            name='svc', owner=self.owner, project=self.project,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def _write_encrypted(self, payload: bytes, key: str) -> str:
        encrypted = _encrypt_chunked(payload, key)
        fd, path = tempfile.mkstemp(suffix='.tar.gz.enc')
        with os.fdopen(fd, 'wb') as f:
            f.write(encrypted)
        return path

    @skipIf(IS_WINDOWS, "Windows ignores POSIX mode bits in os.chmod")
    def test_decrypted_file_has_0o600_mode(self):
        key = Fernet.generate_key().decode()
        payload = b'backup-payload-for-mode-test'
        encrypted_path = self._write_encrypted(payload, key)
        try:
            with override_settings(BACKUP_REQUIRE_ENCRYPTION=True):
                decrypted = BackupService.decrypt_backup(encrypted_path, key)
            try:
                mode = stat.S_IMODE(os.stat(decrypted).st_mode)
                self.assertEqual(mode, 0o600)
            finally:
                BackupService.cleanup_decrypted_path(decrypted)
        finally:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)

    @skipIf(IS_WINDOWS, "Windows ignores POSIX mode bits in os.chmod")
    def test_parent_dir_uses_0o700_mode(self):
        key = Fernet.generate_key().decode()
        payload = b'backup-payload-for-dir-test'
        encrypted_path = self._write_encrypted(payload, key)
        try:
            decrypted = BackupService.decrypt_backup(encrypted_path, key)
            try:
                parent = os.path.dirname(os.path.abspath(decrypted))
                dir_mode = stat.S_IMODE(os.stat(parent).st_mode)
                self.assertEqual(dir_mode, 0o700)
                self.assertTrue(
                    os.path.basename(parent).startswith('smsly-decrypted-'),
                )
            finally:
                BackupService.cleanup_decrypted_path(decrypted)
        finally:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)

    def test_decrypted_file_and_parent_dir_removed_after_cleanup(self):
        key = Fernet.generate_key().decode()
        payload = b'backup-payload-for-cleanup-test'
        encrypted_path = self._write_encrypted(payload, key)
        try:
            decrypted = BackupService.decrypt_backup(encrypted_path, key)
            parent = os.path.dirname(os.path.abspath(decrypted))
            self.assertTrue(os.path.exists(decrypted))
            self.assertTrue(os.path.isdir(parent))
            BackupService.cleanup_decrypted_path(decrypted)
            self.assertFalse(os.path.exists(decrypted))
            self.assertFalse(os.path.isdir(parent))
        finally:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)

    def test_no_leftover_decrypted_dirs_in_tmp(self):
        key = Fernet.generate_key().decode()
        payload = b'backup-payload-for-leak-test'
        encrypted_path = self._write_encrypted(payload, key)
        tmp_dir = tempfile.gettempdir()
        try:
            decrypted = BackupService.decrypt_backup(encrypted_path, key)
            parent = os.path.dirname(os.path.abspath(decrypted))
            self.assertTrue(os.path.isdir(parent))
            BackupService.cleanup_decrypted_path(decrypted)
            self.assertFalse(
                os.path.isdir(parent),
                f'Expected private dir to be removed: {parent}',
            )
            leftovers = [
                d for d in os.listdir(tmp_dir)
                if d.startswith('smsly-decrypted-')
            ]
            self.assertNotIn(os.path.basename(parent), leftovers)
        finally:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)

    def test_download_view_removes_decrypted_file(self):
        key = Fernet.generate_key().decode()
        payload = b'service-backup-download-payload'
        encrypted_path = self._write_encrypted(payload, key)
        try:
            backup = ServiceBackup.objects.create(
                service=self.service,
                status='COMPLETED',
                file_path=encrypted_path,
                size_bytes=len(payload),
            )
            signed = _make_signed(str(backup.id))
            from django.urls import reverse
            tmp_dir = tempfile.gettempdir()
            leftovers_before = set(os.listdir(tmp_dir))
            with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}):
                with override_settings(BACKUP_REQUIRE_ENCRYPTION=True):
                    response = self.client.get(
                        reverse('backup-download', args=[backup.id])
                        + f'?signed={signed}',
                    )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                b''.join(response.streaming_content), payload,
            )
            leftovers_after = set(os.listdir(tmp_dir))
            new_dirs = (
                leftovers_after - leftovers_before
            ) & {
                d for d in os.listdir(tmp_dir)
                if d.startswith('smsly-decrypted-')
            }
            self.assertEqual(
                new_dirs, set(),
                f'Decrypted dirs leaked: {new_dirs}',
            )
        finally:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)


class ServerBackupDecryptedCleanupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='srv-dec-admin', password='x',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def _write_encrypted(self, payload: bytes, key: str) -> str:
        encrypted = _encrypt_chunked(payload, key)
        fd, path = tempfile.mkstemp(suffix='.tar.gz.enc')
        with os.fdopen(fd, 'wb') as f:
            f.write(encrypted)
        return path

    def test_server_backup_download_removes_decrypted_file(self):
        key = Fernet.generate_key().decode()
        payload = b'server-backup-download-payload'
        encrypted_path = self._write_encrypted(payload, key)
        try:
            backup = ServerBackup.objects.create(
                status='COMPLETED',
                file_path=encrypted_path,
                size_bytes=len(payload),
            )
            signed = _make_signed(str(backup.id))
            from django.urls import reverse
            tmp_dir = tempfile.gettempdir()
            leftovers_before = set(os.listdir(tmp_dir))
            with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}):
                with override_settings(BACKUP_REQUIRE_ENCRYPTION=True):
                    response = self.client.get(
                        reverse('server-backup-download', args=[backup.id])
                        + f'?signed={signed}',
                    )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                b''.join(response.streaming_content), payload,
            )
            leftovers_after = set(os.listdir(tmp_dir))
            new_dirs = (
                leftovers_after - leftovers_before
            ) & {
                d for d in os.listdir(tmp_dir)
                if d.startswith('smsly-decrypted-')
            }
            self.assertEqual(
                new_dirs, set(),
                f'Decrypted dirs leaked: {new_dirs}',
            )
        finally:
            if os.path.exists(encrypted_path):
                os.remove(encrypted_path)
