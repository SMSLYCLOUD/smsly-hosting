"""
Celery task argument encryption utility.

SEC-ZT-006: Encrypts sensitive task arguments (passwords, secrets) so they
are not visible in the Celery broker (Redis/RabbitMQ) or result backend.

Usage:
    from .task_encryption import encrypt_arg, decrypt_arg

    @shared_task
    def my_task(encrypted_password: str):
        password = decrypt_arg(encrypted_password)
        ...

    # When calling:
    my_task.delay(encrypt_arg("super-secret-password"))
"""

import os
from base64 import urlsafe_b64decode, urlsafe_b64encode

from django.conf import settings

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None  # type: ignore
    InvalidToken = None  # type: ignore


def _get_task_encryption_key() -> bytes | None:
    """
    Get the key for Celery task argument encryption.

    Uses FIELD_ENCRYPTION_KEY by default (already configured for Fernet).
    Can be overridden with SMSLY_TASK_ENCRYPTION_KEY env var.
    Falls back to None (no encryption) if not configured.
    """
    key_str = os.environ.get("SMSLY_TASK_ENCRYPTION_KEY", "")
    if not key_str:
        key_str = str(getattr(settings, "FIELD_ENCRYPTION_KEY", ""))
    if not key_str:
        return None
    try:
        return key_str.encode() if isinstance(key_str, str) else key_str
    except Exception:
        return None


def encrypt_arg(value: str) -> str:
    """
    Encrypt a sensitive task argument.

    Returns the encrypted value prefixed with 'enc:' so decryption knows
    how to handle it. If encryption is not configured, returns the raw value.
    """
    key = _get_task_encryption_key()
    if not key or Fernet is None:
        return value  # No encryption configured

    try:
        f = Fernet(key)
        encrypted = f.encrypt(value.encode())
        return "enc:" + urlsafe_b64encode(encrypted).decode()
    except Exception:
        return value  # Fall back to plaintext on error


def decrypt_arg(encrypted_value: str) -> str:
    """
    Decrypt a task argument that was encrypted with encrypt_arg().

    If the value does not start with 'enc:', it is returned as-is (plaintext).
    """
    if not encrypted_value.startswith("enc:"):
        return encrypted_value  # Not encrypted

    key = _get_task_encryption_key()
    if not key or Fernet is None:
        return encrypted_value  # Cannot decrypt

    try:
        f = Fernet(key)
        raw = urlsafe_b64decode(encrypted_value[4:])
        return f.decrypt(raw).decode()
    except (InvalidToken, Exception):
        return encrypted_value  # Cannot decrypt


def obfuscate_arg(value: str, visible_chars: int = 4) -> str:
    """
    Obfuscate a sensitive value for logging.

    Shows first N characters followed by '...'(e.g., "abc...").
    """
    if not value or len(value) <= visible_chars:
        return value
    return value[:visible_chars] + "..."
