"""
Tests for V2 backup encryption header + cross-master key import.

The V2 header (``SMSLY-BACKUP-AESGCM-V2\n`` + 4-byte ``key_id`` +
4-byte ``fingerprint`` + 8-byte nonce prefix) lets a target master
restore a backup created on a different master. The operator runs
``POST /backups/import-key/`` with the source's
``BACKUP_ENCRYPTION_KEY`` and the ``key_id`` from the source's
``GET /backups/{id}/header/``; the target stores the row, and
subsequent restores resolve the key by ``key_id`` automatically.

These tests are hermetic — they only touch the BackupEncryptionKey
model, the encrypt/decrypt round-trip, and the public service
methods (``import_backup_key``, ``read_v2_header``, ``lookup_key_by_id``).
No DB row creation requires external services.
"""
import contextlib
import os
import tempfile
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models_backup import BackupEncryptionKey
from apps.deployments.services.backup_service import (
    _CHUNKED_BACKUP_FINGERPRINT_BYTES,
    _CHUNKED_BACKUP_KEY_ID_BYTES,
    _CHUNKED_BACKUP_V3_MAGIC,
    BackupKeyCollisionError,
    BackupService,
    UnknownBackupKeyIdError,
)

User = get_user_model()


class BackupKeyFingerprintTests(TestCase):
    """compute_backup_key_fingerprint returns 8-char hex of first 4
    bytes of SHA-256(raw 32-byte AES key)."""

    def test_fingerprint_is_8_hex_chars(self):
        key = Fernet.generate_key().decode()
        fp = BackupService.compute_backup_key_fingerprint(key)
        self.assertEqual(len(fp), 8)
        int(fp, 16)

    def test_fingerprint_is_deterministic(self):
        key = Fernet.generate_key().decode()
        self.assertEqual(
            BackupService.compute_backup_key_fingerprint(key),
            BackupService.compute_backup_key_fingerprint(key),
        )

    def test_different_keys_produce_different_fingerprints(self):
        a = Fernet.generate_key().decode()
        b = Fernet.generate_key().decode()
        self.assertNotEqual(
            BackupService.compute_backup_key_fingerprint(a),
            BackupService.compute_backup_key_fingerprint(b),
        )

    def test_invalid_key_raises_value_error(self):
        with self.assertRaises(ValueError):
            BackupService.compute_backup_key_fingerprint("not-a-fernet-key")


class V2HeaderRoundTripTests(TestCase):
    """Encrypt with key K → write V2 header with key_id/fingerprint;
    decrypt with K → original bytes."""

    def _write_fake_archive(self, payload: bytes) -> str:
        tmp = tempfile.mkdtemp(prefix="v2hdr-")
        path = os.path.join(tmp, f"{uuid.uuid4().hex}.tar.gz")
        with open(path, "wb") as f:
            f.write(payload)
        self.addCleanup(lambda: self._rm(path))
        return path

    @staticmethod
    def _rm(path):
        with contextlib.suppress(OSError):
            os.remove(path)
        parent = os.path.dirname(path)
        with contextlib.suppress(OSError):
            os.rmdir(parent)

    def test_encrypt_writes_v3_magic_with_key_id_and_fingerprint(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
            payload = b"hello-v3-archive"
            archive = self._write_fake_archive(payload)
            enc_path = BackupService()._maybe_encrypt(archive)
            self.assertTrue(enc_path.endswith('.enc'))
            with open(enc_path, 'rb') as f:
                magic = f.read(len(_CHUNKED_BACKUP_V3_MAGIC))
                self.assertEqual(magic, _CHUNKED_BACKUP_V3_MAGIC)
                key_id = f.read(_CHUNKED_BACKUP_KEY_ID_BYTES)
                fingerprint = f.read(_CHUNKED_BACKUP_FINGERPRINT_BYTES)
            self.assertEqual(len(key_id), _CHUNKED_BACKUP_KEY_ID_BYTES)
            self.assertEqual(len(fingerprint), _CHUNKED_BACKUP_FINGERPRINT_BYTES)
            self.assertEqual(
                fingerprint.hex(),
                BackupService.compute_backup_key_fingerprint(key),
            )
            dec_path = BackupService.decrypt_backup(enc_path, key)
            with open(dec_path, 'rb') as f:
                self.assertEqual(f.read(), payload)
            BackupService.cleanup_decrypted_path(dec_path)

    def test_register_active_key_creates_db_row(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
            BackupEncryptionKey.objects.all().delete()
            info = BackupService.resolve_or_register_active_key(key)
            self.assertIn('key_id', info)
            self.assertIn('fingerprint', info)
            self.assertTrue(info['created'])
            row = BackupEncryptionKey.objects.get(key_id=info['key_id'])
            self.assertEqual(row.fingerprint, info['fingerprint'])
            self.assertEqual(row.source, 'AUTO')
            self.assertTrue(row.is_active)
            self.assertEqual(row.key_material_encrypted, key)

    def test_resolve_active_key_is_idempotent(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
            BackupEncryptionKey.objects.all().delete()
            first = BackupService.resolve_or_register_active_key(key)
            second = BackupService.resolve_or_register_active_key(key)
            self.assertEqual(first['key_id'], second['key_id'])
            self.assertEqual(first['fingerprint'], second['fingerprint'])
            self.assertFalse(second['created'])
            self.assertEqual(
                BackupEncryptionKey.objects.filter(is_active=True).count(),
                1,
            )


class V2HeaderDecryptTests(TestCase):
    """Decrypt behaviour for V2 backups when the env key doesn't match."""

    def _write_fake_archive(self, payload: bytes) -> str:
        tmp = tempfile.mkdtemp(prefix="v2dec-")
        path = os.path.join(tmp, f"{uuid.uuid4().hex}.tar.gz")
        with open(path, "wb") as f:
            f.write(payload)
        self.addCleanup(lambda: self._rm(path))
        return path

    @staticmethod
    def _rm(path):
        with contextlib.suppress(OSError):
            os.remove(path)
        parent = os.path.dirname(path)
        with contextlib.suppress(OSError):
            os.rmdir(parent)

    def test_v1_backup_still_decrypts_with_env_key(self):
        """Backward compat: V1 (no key_id in header) backups written
        before this change must still decrypt with the env key."""
        key = Fernet.generate_key().decode()
        payload = b"legacy-v1-payload"
        archive = self._write_fake_archive(payload)
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
            enc_path = BackupService()._maybe_encrypt(archive)
            with open(enc_path, 'rb') as f:
                magic = f.read(len(_CHUNKED_BACKUP_V3_MAGIC))
            self.assertEqual(magic, _CHUNKED_BACKUP_V3_MAGIC)
            dec_path = BackupService.decrypt_backup(enc_path, key)
            with open(dec_path, 'rb') as f:
                self.assertEqual(f.read(), payload)
            BackupService.cleanup_decrypted_path(dec_path)

    def test_v2_with_wrong_env_key_and_no_db_row_raises(self):
        """When the env key's fingerprint doesn't match the V2 header
        AND there's no imported key with the header's key_id, the
        decrypt path raises UnknownBackupKeyIdError carrying the
        key_id + fingerprint so the API can return a 400.

        Simulates the cross-master scenario: encrypt on master A
        (which auto-registers source_key in A's DB), then delete the
        auto-registered row to simulate the backup arriving on master
        B (which has never seen source_key).
        """
        source_key = Fernet.generate_key().decode()
        target_key = Fernet.generate_key().decode()
        payload = b"cross-master-payload"
        archive = self._write_fake_archive(payload)
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': source_key}, clear=False):
            enc_path = BackupService()._maybe_encrypt(archive)
            header = BackupService.read_v2_header(enc_path)
            BackupEncryptionKey.objects.all().delete()
            with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': target_key}, clear=False):
                with self.assertRaises(UnknownBackupKeyIdError) as ctx:
                    BackupService.decrypt_backup(enc_path, target_key)
            self.assertEqual(ctx.exception.key_id, header['key_id'])
            self.assertEqual(ctx.exception.fingerprint, header['fingerprint'])

    def test_v2_with_imported_key_succeeds(self):
        """The cross-master happy path: source encrypts with source
        key; target imports the source key by key_id; target's env
        key is unrelated; decrypt succeeds via the imported row.

        Simulates the cross-master scenario by deleting the
        auto-registered row from the source side after encryption.
        """
        source_key = Fernet.generate_key().decode()
        target_key = Fernet.generate_key().decode()
        payload = b"cross-master-imported-payload"
        archive = self._write_fake_archive(payload)
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': source_key}, clear=False):
            enc_path = BackupService()._maybe_encrypt(archive)
            header = BackupService.read_v2_header(enc_path)
            self.assertNotEqual(
                BackupService.compute_backup_key_fingerprint(source_key),
                BackupService.compute_backup_key_fingerprint(target_key),
            )
            BackupEncryptionKey.objects.all().delete()
            BackupService.import_backup_key(
                key_id=header['key_id'],
                key_material=source_key,
                label='cross-master-test',
            )
            with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': target_key}, clear=False):
                dec_path = BackupService.decrypt_backup(enc_path, target_key)
            with open(dec_path, 'rb') as f:
                self.assertEqual(f.read(), payload)
            BackupService.cleanup_decrypted_path(dec_path)


class ImportBackupKeyTests(TestCase):
    """import_backup_key: validations, idempotency, collision detection."""

    def test_import_creates_row_with_source_imported(self):
        key = Fernet.generate_key().decode()
        result = BackupService.import_backup_key(
            key_id='a1b2c3d4',
            key_material=key,
            label='master-a-test',
        )
        self.assertTrue(result['created'])
        self.assertEqual(result['key_id'], 'a1b2c3d4')
        self.assertEqual(result['source'], 'IMPORTED')
        self.assertEqual(result['fingerprint'],
                         BackupService.compute_backup_key_fingerprint(key))
        row = BackupEncryptionKey.objects.get(key_id='a1b2c3d4')
        self.assertFalse(row.is_active)
        self.assertEqual(row.label, 'master-a-test')
        self.assertEqual(row.key_material_encrypted, key)

    def test_import_is_idempotent_on_same_key_material(self):
        key = Fernet.generate_key().decode()
        first = BackupService.import_backup_key(
            key_id='deadbeef', key_material=key,
        )
        second = BackupService.import_backup_key(
            key_id='deadbeef', key_material=key,
        )
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(
            BackupEncryptionKey.objects.filter(key_id='deadbeef').count(),
            1,
        )

    def test_import_collision_raises(self):
        key_a = Fernet.generate_key().decode()
        key_b = Fernet.generate_key().decode()
        BackupService.import_backup_key(key_id='cafebabe', key_material=key_a)
        with self.assertRaises(BackupKeyCollisionError):
            BackupService.import_backup_key(key_id='cafebabe', key_material=key_b)

    def test_import_validates_key_id_length(self):
        key = Fernet.generate_key().decode()
        with self.assertRaises(ValueError):
            BackupService.import_backup_key(key_id='short', key_material=key)

    def test_import_validates_key_id_is_hex(self):
        key = Fernet.generate_key().decode()
        with self.assertRaises(ValueError):
            BackupService.import_backup_key(key_id='zzzzzzzz', key_material=key)

    def test_import_validates_key_material_is_fernet(self):
        with self.assertRaises(ValueError):
            BackupService.import_backup_key(
                key_id='a1b2c3d4', key_material='not-a-fernet-key',
            )


class V2HeaderReadTests(TestCase):
    """read_v2_header returns key_id + fingerprint from a V2 file."""

    def test_read_v2_header_returns_expected_fields(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
            tmp = tempfile.mkdtemp(prefix="v2rd-")
            archive = os.path.join(tmp, f"{uuid.uuid4().hex}.tar.gz")
            with open(archive, 'wb') as f:
                f.write(b"v2-header-test")
            try:
                enc_path = BackupService()._maybe_encrypt(archive)
                info = BackupService.read_v2_header(enc_path)
                self.assertIn(info['magic'], ('V2', 'V3'))
                self.assertEqual(len(info['key_id']), 8)
                self.assertEqual(len(info['fingerprint']), 8)
                self.assertEqual(
                    info['fingerprint'],
                    BackupService.compute_backup_key_fingerprint(key),
                )
            finally:
                self._rm(enc_path)
                self._rm(archive)

    def test_read_v2_header_raises_for_v1(self):
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': ''}, clear=False):
            tmp = tempfile.mkdtemp(prefix="v2rd-v1-")
            path = os.path.join(tmp, f"{uuid.uuid4().hex}.bin")
            with open(path, 'wb') as f:
                f.write(b"plain-bytes")
            try:
                with self.assertRaises(ValueError):
                    BackupService.read_v2_header(path)
            finally:
                self._rm(path)

    @staticmethod
    def _rm(path):
        with contextlib.suppress(OSError):
            os.remove(path)
        parent = os.path.dirname(path)
        with contextlib.suppress(OSError):
            os.rmdir(parent)
