"""Backup-specific exceptions and format constants."""
import re


class BackupEncryptionRequired(Exception):
    """Raised when BACKUP_REQUIRE_ENCRYPTION is set but BACKUP_ENCRYPTION_KEY is missing."""
    pass


class UnknownBackupKeyIdError(Exception):
    """Raised when a V2 backup's key_id is not registered on this master.

    The caller should respond with a 400 + key_id + expected_fingerprint
    so the operator can either re-run with the correct key or call
    ``POST /backups/import-key/`` to register the source's key on this
    master. After import, the restore can be retried and the key will
    resolve automatically.
    """
    def __init__(self, key_id: str, fingerprint: str, message: str = ''):
        self.key_id = key_id
        self.fingerprint = fingerprint
        super().__init__(message or f"Unknown backup key_id={key_id}")


class BackupKeyCollisionError(Exception):
    """Raised when importing a key whose key_id collides with an existing
    row that has different key material (likely a 1-in-2^32 random collision
    or an attempted key-swap attack)."""
    pass


_CHUNKED_BACKUP_MAGIC = b"SMSLY-BACKUP-AESGCM-V1\n"
_CHUNKED_BACKUP_V2_MAGIC = b"SMSLY-BACKUP-AESGCM-V2\n"
_CHUNKED_BACKUP_V3_MAGIC = b"SMSLY-BACKUP-AESGCM-V3\n"
_CHUNKED_BACKUP_NONCE_PREFIX_BYTES = 8
_CHUNKED_BACKUP_KEY_ID_BYTES = 4
_CHUNKED_BACKUP_FINGERPRINT_BYTES = 4
_DEFAULT_CRYPTO_CHUNK_SIZE = 4 * 1024 * 1024
_FERNET_HEADER_SIZE = 1 + 8 + 16
_FERNET_HMAC_SIZE = 32

_DEFAULT_MAX_BACKUP_SIZE = 50 * 1024 * 1024 * 1024

_SENSITIVE_ENV_PATTERN = re.compile(
    r'(PASSWORD|SECRET|KEY|TOKEN|CREDENTIAL|API_KEY|PRIVATE)',
    re.IGNORECASE,
)
