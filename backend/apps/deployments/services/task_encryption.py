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

import logging
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode

from django.conf import settings

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None  # type: ignore
    InvalidToken = None  # type: ignore

logger = logging.getLogger(__name__)


class TaskEncryptionError(RuntimeError):
    """Raised when task argument encryption/decryption fails under strict mode."""


def _is_strict_mode() -> bool:
    explicit = os.environ.get("SMSLY_TASK_ENCRYPTION_STRICT", "").strip().lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False
    # Never flip platform-wide Celery tasks into strict mode merely because SMSLY_CRYPTD_URL is set.
    return bool(os.environ.get("SMSLY_TASK_ENCRYPTION_KEY", "").strip())


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


def encrypt_arg(value: str, *, service: object | None = None, service_id: str | None = None) -> str:
    """
    Encrypt a sensitive task argument.

    Uses grid-cryptd transit encryption ONLY when scoped to an opted-in service
    (or explicit service_id), otherwise uses Fernet ('enc:') when configured.
    """
    strict = _is_strict_mode()
    target_service_id = str(getattr(service, "id", "") or service_id or "").strip()

    if target_service_id and target_service_id != "paas-control-plane":
        try:
            from apps.core.crypto_daemon import cryptd_client, is_service_encryption_enabled
            service_opted = is_service_encryption_enabled(service) if service is not None else True
            if service_opted and cryptd_client.is_enabled():
                token = cryptd_client.encrypt_text(value, service_id=target_service_id, context="celery-task-arg")
                if token:
                    return f"enc:{token}"
        except Exception as exc:
            logger.error("grid-cryptd task_encryption failed: %s", exc)
            if strict:
                raise TaskEncryptionError("grid-cryptd task argument encryption failed") from exc

    key = _get_task_encryption_key()
    if not key or Fernet is None:
        if strict:
            raise TaskEncryptionError("No task encryption key configured in strict mode")
        return value

    try:
        f = Fernet(key)
        encrypted = f.encrypt(value.encode())
        return "enc:" + urlsafe_b64encode(encrypted).decode()
    except Exception as exc:
        logger.error("Fernet task_encryption failed: %s", exc)
        if strict or key:
            raise TaskEncryptionError("Fernet task argument encryption failed") from exc
        return value


def decrypt_arg(encrypted_value: str, *, service_id: str | None = None) -> str:
    """
    Decrypt a task argument that was encrypted with encrypt_arg().

    Supports 'enc:grid:...' (service-scoped grid-cryptd transit tokens) and legacy 'enc:' Fernet.
    """
    if not isinstance(encrypted_value, str) or not encrypted_value.startswith("enc:"):
        return encrypted_value  # Not encrypted

    payload = encrypted_value[4:]
    if payload.startswith("grid:"):
        svc_id = str(service_id or "").strip()
        if not svc_id:
            if _is_strict_mode():
                raise TaskEncryptionError("service_id is required to decrypt grid-cryptd task argument")
            return encrypted_value
        try:
            from apps.core.crypto_daemon import cryptd_client
            return cryptd_client.decrypt_text(payload, service_id=svc_id, context="celery-task-arg")
        except Exception as exc:
            logger.error("grid-cryptd task_decryption failed: %s", exc)
            if _is_strict_mode():
                raise TaskEncryptionError("grid-cryptd task argument decryption failed") from exc
            return encrypted_value

    key = _get_task_encryption_key()
    if not key or Fernet is None:
        if _is_strict_mode():
            raise TaskEncryptionError("Task decryption key missing in strict mode")
        return encrypted_value

    try:
        f = Fernet(key)
        raw = urlsafe_b64decode(payload)
        return f.decrypt(raw).decode()
    except (InvalidToken, Exception) as exc:
        if _is_strict_mode():
            raise TaskEncryptionError("Task argument decryption failed") from exc
        return encrypted_value


def obfuscate_arg(value: str, visible_chars: int = 4) -> str:
    """
    Obfuscate a sensitive value for logging.

    Shows first N characters followed by '...'(e.g., "abc...").
    """
    if not value or len(value) <= visible_chars:
        return value
    return value[:visible_chars] + "..."
