import uuid
from urllib.parse import urlparse
from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings
from encrypted_model_fields.fields import EncryptedCharField
from .models_core import Service


def _is_internal_http_host(host: str) -> bool:
    if not host:
        return False
    if host.startswith('localhost') or host.startswith('127.'):
        return True
    private_prefixes = (
        '10.', '192.168.',
        '172.16.', '172.17.', '172.18.', '172.19.', '172.20.',
        '172.21.', '172.22.', '172.23.', '172.24.', '172.25.',
        '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.',
    )
    if any(host.startswith(p) for p in private_prefixes):
        return True
    if host.startswith('smsly-') or host in ('minio', 'registry'):
        return True
    if host.endswith('.internal'):
        return True
    return False


def validate_endpoint_url(url: str) -> None:
    """Validate an S3/R2/MinIO endpoint URL.

    Empty URLs are allowed (means use provider default).
    Only http and https schemes are accepted.
    Plain http is only allowed for internal hosts to prevent forcing
    unencrypted transit to attacker-controlled hosts.
    """
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValidationError(
            f'Endpoint must use http or https; got {parsed.scheme!r}',
        )
    if parsed.scheme == 'http':
        host = (parsed.hostname or '').lower()
        if not _is_internal_http_host(host):
            raise ValidationError(
                'http:// endpoint only allowed for localhost/internal hosts',
            )


class ServiceBackup(models.Model):
    """Full snapshot of a service: container state + volumes + env vars + addons."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='backups')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    status = models.CharField(choices=[
        ('PENDING', 'Pending'), ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'), ('FAILED', 'Failed'),
    ], default='PENDING', max_length=20)
    backup_type = models.CharField(choices=[
        ('MANUAL', 'Manual'), ('SCHEDULED', 'Scheduled'),
        ('PRE_TRANSFER', 'Pre-Transfer'),
    ], default='MANUAL', max_length=20)
    file_path = models.CharField(max_length=500, blank=True)  # path to tarball
    size_bytes = models.BigIntegerField(default=0)
    metadata = models.JSONField(default=dict)  # snapshot of env vars, resources, config
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

class ServerBackup(models.Model):
    """Full server export: all services + platform config + Traefik + SSL certs."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    status = models.CharField(max_length=20, default='PENDING')
    file_path = models.CharField(max_length=500, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    services_included = models.JSONField(default=list)
    error_message = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

class BackupSchedule(models.Model):
    """Cron-based backup schedule per service or server-wide."""
    service = models.ForeignKey(Service, on_delete=models.CASCADE, null=True, blank=True)
    is_server_wide = models.BooleanField(default=False)
    cron_expression = models.CharField(max_length=100, default='0 3 * * *')  # daily 3am
    retention_days = models.IntegerField(default=7)
    enabled = models.BooleanField(default=True)
    last_run = models.DateTimeField(null=True)
    next_run = models.DateTimeField(null=True)
    # ── S3 / object storage destination (optional) ──────────────────────────
    storage_backend = models.CharField(
        max_length=20,
        choices=[('local', 'Local'), ('s3', 'S3 / R2 / MinIO')],
        default='local',
    )
    s3_bucket = models.CharField(max_length=255, blank=True, default='')
    s3_region = models.CharField(max_length=100, blank=True, default='us-east-1')
    s3_endpoint = models.CharField(
        max_length=500, blank=True, default='',
        help_text='Custom endpoint for R2/MinIO. Leave blank for AWS S3.',
    )
    s3_access_key = EncryptedCharField(max_length=255, blank=True, default='')
    s3_secret_key = EncryptedCharField(max_length=255, blank=True, default='')

    def clean(self):
        super().clean()
        validate_endpoint_url(self.s3_endpoint)


class BackupEncryptionKey(models.Model):
    """Track every Fernet key ever used to encrypt backups on this master.

    Enables cross-master restore: the V2 backup header carries a
    ``key_id`` (random 4 bytes) + ``fingerprint`` (first 4 bytes of
    SHA-256 of the raw AES key). When a target master reads a V2
    backup, it looks up the key by ``key_id`` here. If the row
    exists, the target can decrypt directly. If not, the operator
    must call ``POST /backups/import-key/`` to import the source
    master's key material; the row is then created with the
    source's ``key_id`` and the imported key is used.

    The key material is encrypted at rest with
    ``FIELD_ENCRYPTION_KEY`` via :class:`EncryptedCharField` so a
    raw DB read does not yield decryption capability.

    The first row (auto-created from the master's
    ``BACKUP_ENCRYPTION_KEY`` on first use) has ``is_active=True``;
    imported keys have ``is_active=False``. Only one row should
    be active at a time — when the operator rotates the
    ``BACKUP_ENCRYPTION_KEY``, a new active row is created on the
    next backup.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    key_id = models.CharField(
        max_length=8,
        unique=True,
        db_index=True,
        help_text='4 random bytes as 8-char hex; matches the V2 backup header key_id.',
    )
    fingerprint = models.CharField(
        max_length=8,
        db_index=True,
        help_text='First 4 bytes of SHA-256(raw_key) as 8-char hex; matches V2 header fingerprint.',
    )
    key_material_encrypted = EncryptedCharField(
        max_length=512,
        help_text='Fernet BACKUP_ENCRYPTION_KEY, encrypted at rest with FIELD_ENCRYPTION_KEY.',
    )
    label = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Operator label e.g. "master-a-imported-2026-06-14".',
    )
    source = models.CharField(
        max_length=20,
        choices=[
            ('AUTO', 'Auto-generated by this master'),
            ('IMPORTED', 'Imported from another master'),
        ],
        default='AUTO',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='True for the row matching the current BACKUP_ENCRYPTION_KEY env var.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['fingerprint', 'is_active']),
        ]

    def __str__(self):
        return f'BackupEncryptionKey(key_id={self.key_id}, fp={self.fingerprint}, source={self.source})'
