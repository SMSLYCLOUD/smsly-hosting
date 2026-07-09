"""
Tests for V3 backup encryption format.

The V3 header (``SMSLY-BACKUP-AESGCM-V3\n`` + 4-byte ``key_id`` +
4-byte ``fingerprint`` + 8-byte nonce prefix) is identical to V2
except:

1. The trailing ``0u32`` (end-of-chunks marker) is preceded by an
   *encrypted* ``EOF`` chunk whose plaintext is exactly the 3 bytes
   ``b"EOF"``.  This lets the decryptor detect truncation attacks
   (an attacker cannot forge the final chunk without the key).
2. ``_maybe_encrypt`` writes the V3 magic string on new backups.
3. ``decrypt_backup`` auto-detects V3 from the magic and dispatches
   to ``_decrypt_v3_chunked_backup``, which validates the EOF marker.
"""
import base64
import contextlib
import os
import struct
import tempfile
import uuid
from unittest.mock import patch

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models_backup import BackupEncryptionKey
from apps.deployments.services.backup_service import (
    _CHUNKED_BACKUP_FINGERPRINT_BYTES,
    _CHUNKED_BACKUP_KEY_ID_BYTES,
    _CHUNKED_BACKUP_MAGIC,
    _CHUNKED_BACKUP_NONCE_PREFIX_BYTES,
    _CHUNKED_BACKUP_V2_MAGIC,
    _CHUNKED_BACKUP_V3_MAGIC,
    BackupService,
)

User = get_user_model()


def _v3_constants():
    """Return the constants module for test assertions."""
    return {
        'V3_MAGIC': _CHUNKED_BACKUP_V3_MAGIC,
        'V2_MAGIC': _CHUNKED_BACKUP_V2_MAGIC,
        'V1_MAGIC': _CHUNKED_BACKUP_MAGIC,
    }


class V3MagicConstantsTest(TestCase):
    """V3 magic is defined and distinct from V2/V1."""

    def test_v3_magic_is_distinct(self):
        c = _v3_constants()
        self.assertNotEqual(c['V3_MAGIC'], c['V2_MAGIC'])
        self.assertNotEqual(c['V3_MAGIC'], c['V1_MAGIC'])

    def test_v3_magic_ends_with_v3_newline(self):
        self.assertTrue(
            _CHUNKED_BACKUP_V3_MAGIC.endswith(b'V3\n'),
            msg='V3 magic should end with "V3\\n"',
        )


class V3EncryptWritesV3MagicTest(TestCase):
    """_maybe_encrypt writes V3 magic (not V2) when no format override."""

    @staticmethod
    def _write_fake_archive(payload: bytes = b"hello-v3") -> str:
        tmp = tempfile.mkdtemp(prefix="v3-")
        path = os.path.join(tmp, f"{uuid.uuid4().hex}.tar.gz")
        with open(path, "wb") as f:
            f.write(payload)
        return path

    @staticmethod
    def _rm(path):
        with contextlib.suppress(OSError):
            os.remove(path)
        parent = os.path.dirname(path)
        with contextlib.suppress(OSError):
            os.rmdir(parent)

    def test_encrypt_writes_v3_magic(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
            archive = self._write_fake_archive(b"v3-magic-test")
            try:
                enc_path = BackupService()._maybe_encrypt(archive)
                self.assertTrue(enc_path.endswith('.enc'))
                with open(enc_path, 'rb') as f:
                    magic = f.read(len(_CHUNKED_BACKUP_V3_MAGIC))
                self.assertEqual(magic, _CHUNKED_BACKUP_V3_MAGIC,
                                 "_maybe_encrypt should write V3 magic")
            finally:
                self._rm(enc_path)
                self._rm(archive)


class V3DecryptBackwardCompatTest(TestCase):
    """V3 decrypt still handles V2 and V1 backups (backward compat)."""

    @staticmethod
    def _write_fake_archive(payload: bytes) -> str:
        tmp = tempfile.mkdtemp(prefix="v3bwd-")
        path = os.path.join(tmp, f"{uuid.uuid4().hex}.tar.gz")
        with open(path, "wb") as f:
            f.write(payload)
        return path

    @staticmethod
    def _rm(path):
        with contextlib.suppress(OSError):
            os.remove(path)
        parent = os.path.dirname(path)
        with contextlib.suppress(OSError):
            os.rmdir(parent)

    def test_decrypt_v3_round_trip(self):
        """Encrypt with V3 → decrypt with decrypt_backup → original bytes."""
        key = Fernet.generate_key().decode()
        payload = b"v3-decrypt-round-trip-" + b"x" * 50000
        archive = self._write_fake_archive(payload)
        try:
            with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
                enc_path = BackupService()._maybe_encrypt(archive)
            with open(enc_path, 'rb') as f:
                magic = f.read(len(_CHUNKED_BACKUP_V3_MAGIC))
            self.assertEqual(magic, _CHUNKED_BACKUP_V3_MAGIC)
            dec_path = BackupService.decrypt_backup(enc_path, key)
            try:
                with open(dec_path, 'rb') as f:
                    self.assertEqual(f.read(), payload)
            finally:
                BackupService.cleanup_decrypted_path(dec_path)
        finally:
            self._rm(enc_path)
            self._rm(archive)

    def test_decrypt_v2_backup_still_works(self):
        """A backup encrypted by an older version (V2 magic) must still
        decrypt with the current code path that dispatches via magic."""
        key = Fernet.generate_key().decode()
        payload = b"v2-backward-compat-payload"
        archive = self._write_fake_archive(payload)

        # Register the key so _resolve_key_for_v2 can look it up by
        # fingerprint matching (comparing passed key's fingerprint
        # against the V2 header).
        from apps.deployments.models_backup import BackupEncryptionKey

        def _v2_encrypt(src_path, enc_key):
            """Simulate writing a V2 backup (used by old code)."""
            raw_key = base64.urlsafe_b64decode(enc_key.encode())
            aesgcm = AESGCM(raw_key)
            fp = BackupService.compute_backup_key_fingerprint(enc_key)
            fp_bytes = bytes.fromhex(fp[:_CHUNKED_BACKUP_FINGERPRINT_BYTES * 2])
            key_id = os.urandom(_CHUNKED_BACKUP_KEY_ID_BYTES)
            key_id_hex = key_id.hex()

            # Register the key in the DB so it can be resolved later
            BackupEncryptionKey.objects.create(
                key_id=key_id_hex,
                fingerprint=fp,
                key_material_encrypted=enc_key,
                source='IMPORTED',
                is_active=False,
            )

            tmp_path = src_path + ".enc.v2tmp"
            try:
                with open(src_path, "rb") as source, open(tmp_path, "wb") as out:
                    nonce_prefix = os.urandom(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
                    out.write(_CHUNKED_BACKUP_V2_MAGIC)
                    out.write(key_id)
                    out.write(fp_bytes)
                    out.write(nonce_prefix)
                    data = source.read()
                    nonce = nonce_prefix + struct.pack(">I", 0)
                    ct = aesgcm.encrypt(nonce, data, None)
                    out.write(struct.pack(">I", len(ct)))
                    out.write(ct)
                    out.write(struct.pack(">I", 0))
                os.remove(src_path)
                os.rename(tmp_path, src_path + ".enc")
                return src_path + ".enc"
            except Exception:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
                raise

        import base64
        try:
            enc_path = _v2_encrypt(archive, key)
            dec_path = BackupService.decrypt_backup(enc_path, key)
            try:
                with open(dec_path, 'rb') as f:
                    self.assertEqual(f.read(), payload)
            finally:
                BackupService.cleanup_decrypted_path(dec_path)
        finally:
            self._rm(enc_path)
            self._rm(archive)

    def test_decrypt_v1_backup_still_works(self):
        """V1 (SMSLY-BACKUP-AESGCM-V1) backups still decrypt."""
        key = Fernet.generate_key().decode()
        payload = b"v1-backward-compat-payload"
        archive = self._write_fake_archive(payload)

        import base64

        def _v1_encrypt(src_path, enc_key):
            raw_key = base64.urlsafe_b64decode(enc_key.encode())
            aesgcm = AESGCM(raw_key)
            tmp_path = src_path + ".enc.v1tmp"
            try:
                with open(src_path, "rb") as source, open(tmp_path, "wb") as out:
                    nonce_prefix = os.urandom(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
                    out.write(_CHUNKED_BACKUP_MAGIC)
                    out.write(nonce_prefix)
                    chunk_index = 0
                    data = source.read()
                    nonce = nonce_prefix + struct.pack(">I", chunk_index)
                    ct = aesgcm.encrypt(nonce, data, None)
                    out.write(struct.pack(">I", len(ct)))
                    out.write(ct)
                    out.write(struct.pack(">I", 0))
                os.remove(src_path)
                os.rename(tmp_path, src_path + ".enc")
                return src_path + ".enc"
            except Exception:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
                raise

        try:
            enc_path = _v1_encrypt(archive, key)
            dec_path = BackupService.decrypt_backup(enc_path, key)
            try:
                with open(dec_path, 'rb') as f:
                    self.assertEqual(f.read(), payload)
            finally:
                BackupService.cleanup_decrypted_path(dec_path)
        finally:
            self._rm(enc_path)
            self._rm(archive)


class V3TruncationDetectionTest(TestCase):
    """V3 EOF marker detects truncated (incomplete) backups."""

    def test_decrypt_truncated_backup_raises_value_error(self):
        """If the EOF chunk is missing or tampered, decrypt raises."""
        key = Fernet.generate_key().decode()
        payload = b"v3-truncation-test-" + b"y" * 10000
        archive = self._write_fake_archive(payload)
        try:
            with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
                enc_path = BackupService()._maybe_encrypt(archive)
            # Truncate the file: remove the last 12 bytes (the encrypted EOF
            # chunk length + ciphertext + the trailing 0u32).
            with open(enc_path, 'rb') as f:
                full = f.read()
            truncated = full[:-50]  # cut enough to remove EOF + trailing 0
            with open(enc_path, 'wb') as f:
                f.write(truncated)
            with self.assertRaises((ValueError, Exception)):
                BackupService.decrypt_backup(enc_path, key)
        finally:
            self._rm(enc_path)
            self._rm(archive)

    @staticmethod
    def _write_fake_archive(payload: bytes) -> str:
        tmp = tempfile.mkdtemp(prefix="v3trunc-")
        path = os.path.join(tmp, f"{uuid.uuid4().hex}.tar.gz")
        with open(path, "wb") as f:
            f.write(payload)
        return path

    @staticmethod
    def _rm(path):
        with contextlib.suppress(OSError):
            os.remove(path)
        parent = os.path.dirname(path)
        with contextlib.suppress(OSError):
            os.rmdir(parent)

    def test_decrypt_v3_without_eof_raises_value_error(self):
        """A V3 backup that was manually stripped of the EOF chunk raises
        ValueError (invalid or missing EOF marker)."""
        key = Fernet.generate_key().decode()
        raw_key = base64.urlsafe_b64decode(key.encode())
        aesgcm = AESGCM(raw_key)
        fp = BackupService.compute_backup_key_fingerprint(key)
        fp_bytes = bytes.fromhex(fp[:_CHUNKED_BACKUP_FINGERPRINT_BYTES * 2])
        key_id = os.urandom(_CHUNKED_BACKUP_KEY_ID_BYTES)
        key_id_hex = key_id.hex()

        # Register key so _resolve_key_for_v2 can find it
        BackupEncryptionKey.objects.create(
            key_id=key_id_hex,
            fingerprint=fp,
            key_material_encrypted=key,
            source='IMPORTED',
            is_active=False,
        )

        tmp = tempfile.mkdtemp(prefix="v3noeof-")
        src = os.path.join(tmp, "data.tar.gz")
        enc_path = os.path.join(tmp, "data.tar.gz.enc")
        try:
            with open(src, "wb") as f:
                f.write(b"no-eof-payload")
            with open(src, "rb") as source, open(enc_path, "wb") as out:
                nonce_prefix = os.urandom(_CHUNKED_BACKUP_NONCE_PREFIX_BYTES)
                out.write(_CHUNKED_BACKUP_V3_MAGIC)
                out.write(key_id)
                out.write(fp_bytes)
                out.write(nonce_prefix)
                data = source.read()
                nonce = nonce_prefix + struct.pack(">I", 0)
                ct = aesgcm.encrypt(nonce, data, None)
                out.write(struct.pack(">I", len(ct)))
                out.write(ct)
                # NO encrypted EOF marker — jump straight to 0u32
                out.write(struct.pack(">I", 0))

            with self.assertRaises(ValueError) as ctx:
                BackupService.decrypt_backup(enc_path, key)
            self.assertIn("EOF", str(ctx.exception))
        finally:
            with contextlib.suppress(OSError):
                os.remove(enc_path)
                os.remove(src)
            with contextlib.suppress(OSError):
                os.rmdir(tmp)


class V3KeyIdAndFingerprintTest(TestCase):
    """V3 backups carry the same key_id/fingerprint header as V2."""

    def test_encrypt_writes_key_id_and_fingerprint(self):
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {'BACKUP_ENCRYPTION_KEY': key}, clear=False):
            tmp = tempfile.mkdtemp(prefix="v3kifp-")
            archive = os.path.join(tmp, f"{uuid.uuid4().hex}.tar.gz")
            with open(archive, 'wb') as f:
                f.write(b"key-id-fp-test")
            try:
                enc_path = BackupService()._maybe_encrypt(archive)
                with open(enc_path, 'rb') as f:
                    f.read(len(_CHUNKED_BACKUP_V3_MAGIC))
                    key_id = f.read(_CHUNKED_BACKUP_KEY_ID_BYTES)
                    fp_bytes = f.read(_CHUNKED_BACKUP_FINGERPRINT_BYTES)
                self.assertEqual(len(key_id), _CHUNKED_BACKUP_KEY_ID_BYTES)
                self.assertEqual(len(fp_bytes), _CHUNKED_BACKUP_FINGERPRINT_BYTES)
                self.assertEqual(
                    fp_bytes.hex(),
                    BackupService.compute_backup_key_fingerprint(key),
                )
            finally:
                with contextlib.suppress(OSError):
                    os.remove(enc_path)
                    os.remove(archive)
                with contextlib.suppress(OSError):
                    os.rmdir(tmp)
