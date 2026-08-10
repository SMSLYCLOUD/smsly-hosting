"""Backup service package — split from monolithic backup_service.py."""

from .core import BackupService

from .exceptions import (
    _CHUNKED_BACKUP_FINGERPRINT_BYTES,
    _CHUNKED_BACKUP_KEY_ID_BYTES,
    _CHUNKED_BACKUP_MAGIC,
    _CHUNKED_BACKUP_NONCE_PREFIX_BYTES,
    _CHUNKED_BACKUP_V2_MAGIC,
    _CHUNKED_BACKUP_V3_MAGIC,
    _DEFAULT_CRYPTO_CHUNK_SIZE,
    _DEFAULT_MAX_BACKUP_SIZE,
    _FERNET_HMAC_SIZE,
    _FERNET_HEADER_SIZE,
    _SENSITIVE_ENV_PATTERN,
    BackupEncryptionRequired,
    BackupKeyCollisionError,
    UnknownBackupKeyIdError,
)

from .helpers import (
    _acquire_service_lock,
    _copy_file_to_container,
    _redact_env_for_backup,
    _release_service_lock,
    _safe_tar_extractall,
)

from .s3 import (
    _get_s3_client,
    _s3_delete_with_retry,
    _s3_download_with_retry,
    _s3_upload_with_retry,
    delete_cloud_backup_object,
    download_from_s3,
    list_s3_objects,
    normalize_s3_key,
    upload_backup_to_s3,
)

from .cloud import (
    _alert_cloud_upload_failed,
    _delete_backup_cloud_object,
    _download_backup_from_cloud,
    _resolve_cloud_config,
    _upload_backup_to_cloud,
)

from .operations import (
    _dump_container_database,
    _emergency_restart_container,
    _emergency_restart_remote_container,
    _remap_domain_on_restore,
    _stop_service_for_restore,
    backup_addon,
    purge_user_backups,
    repair_double_encrypted_env_vars,
)

# Re-export BackupService staticmethods at module level for backward compat
_backup_encryption_required = BackupService._backup_encryption_required
_broadcast_progress = BackupService._broadcast_progress
_crypto_chunk_size = BackupService._crypto_chunk_size
_decode_backup_key = BackupService._decode_backup_key
_decrypt_chunked_backup = BackupService._decrypt_chunked_backup
_decrypt_legacy_fernet_backup = BackupService._decrypt_legacy_fernet_backup
_decrypt_v2_chunked_backup = BackupService._decrypt_v2_chunked_backup
_decrypt_v3_chunked_backup = BackupService._decrypt_v3_chunked_backup
_get_encryption_key = BackupService._get_encryption_key
_get_backups_dir = BackupService._get_backups_dir
_make_private_decrypted_path = BackupService._make_private_decrypted_path
_maybe_encrypt = BackupService._maybe_encrypt
_prune_old_backups = BackupService._prune_old_backups
_read_exact = BackupService._read_exact
_resolve_key_for_v2 = BackupService._resolve_key_for_v2
_split_image_reference = BackupService._split_image_reference
can_decrypt_backup = BackupService.can_decrypt_backup
cleanup_decrypted_path = BackupService.cleanup_decrypted_path
compute_backup_key_fingerprint = BackupService.compute_backup_key_fingerprint
decrypt_backup = BackupService.decrypt_backup
get_encryption_header = BackupService.get_encryption_header
import_backup_key = BackupService.import_backup_key
lookup_key_by_id = BackupService.lookup_key_by_id
read_v2_header = BackupService.read_v2_header
resolve_or_register_active_key = BackupService.resolve_or_register_active_key
stamp_encryption_header_into_metadata = BackupService.stamp_encryption_header_into_metadata

__all__ = [
    "BackupService",
    "BackupEncryptionRequired",
    "BackupKeyCollisionError",
    "UnknownBackupKeyIdError",
    "_acquire_service_lock",
    "_alert_cloud_upload_failed",
    "_backup_encryption_required",
    "_broadcast_progress",
    "_CHUNKED_BACKUP_FINGERPRINT_BYTES",
    "_CHUNKED_BACKUP_KEY_ID_BYTES",
    "_CHUNKED_BACKUP_MAGIC",
    "_CHUNKED_BACKUP_NONCE_PREFIX_BYTES",
    "_CHUNKED_BACKUP_V2_MAGIC",
    "_CHUNKED_BACKUP_V3_MAGIC",
    "_copy_file_to_container",
    "_crypto_chunk_size",
    "_decode_backup_key",
    "_decrypt_chunked_backup",
    "_decrypt_legacy_fernet_backup",
    "_decrypt_v2_chunked_backup",
    "_decrypt_v3_chunked_backup",
    "_DEFAULT_CRYPTO_CHUNK_SIZE",
    "_DEFAULT_MAX_BACKUP_SIZE",
    "_delete_backup_cloud_object",
    "_download_backup_from_cloud",
    "_dump_container_database",
    "_emergency_restart_container",
    "_emergency_restart_remote_container",
    "_FERNET_HMAC_SIZE",
    "_FERNET_HEADER_SIZE",
    "_get_backups_dir",
    "_get_encryption_key",
    "_get_s3_client",
    "_make_private_decrypted_path",
    "_maybe_encrypt",
    "_prune_old_backups",
    "_read_exact",
    "_redact_env_for_backup",
    "_release_service_lock",
    "_remap_domain_on_restore",
    "_resolve_cloud_config",
    "_resolve_key_for_v2",
    "_s3_delete_with_retry",
    "_s3_download_with_retry",
    "_s3_upload_with_retry",
    "_safe_tar_extractall",
    "_SENSITIVE_ENV_PATTERN",
    "_split_image_reference",
    "_stop_service_for_restore",
    "_upload_backup_to_cloud",
    "backup_addon",
    "can_decrypt_backup",
    "cleanup_decrypted_path",
    "compute_backup_key_fingerprint",
    "decrypt_backup",
    "delete_cloud_backup_object",
    "download_from_s3",
    "get_encryption_header",
    "import_backup_key",
    "list_s3_objects",
    "lookup_key_by_id",
    "normalize_s3_key",
    "purge_user_backups",
    "read_v2_header",
    "repair_double_encrypted_env_vars",
    "resolve_or_register_active_key",
    "stamp_encryption_header_into_metadata",
    "upload_backup_to_s3",
]
