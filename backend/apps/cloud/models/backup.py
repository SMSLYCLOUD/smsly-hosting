import uuid
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


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
    return bool(host.endswith('.internal'))


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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)  # type: ignore[var-annotated]
    service = models.ForeignKey('deployments.Service', on_delete=models.CASCADE, related_name='backups')  # type: ignore[var-annotated]
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)  # type: ignore[var-annotated]
    label = models.CharField(max_length=255, blank=True, default='',  # type: ignore[var-annotated]
                             help_text='Human-readable label for quick identification, e.g. "Pre-upgrade backup" or "Cloud restore"')
    status = models.CharField(choices=[  # type: ignore[var-annotated]
        ('PENDING', 'Pending'), ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'), ('FAILED', 'Failed'),
    ], default='PENDING', max_length=20)
    db_only = models.BooleanField(default=False)  # type: ignore[var-annotated]
    backup_type = models.CharField(choices=[  # type: ignore[var-annotated]
        ('MANUAL', 'Manual'), ('SCHEDULED', 'Scheduled'),
        ('PRE_TRANSFER', 'Pre-Transfer'),
    ], default='MANUAL', max_length=20)
    file_path = models.CharField(max_length=500, blank=True)  # type: ignore[var-annotated]  # path to tarball
    size_bytes = models.BigIntegerField(default=0)  # type: ignore[var-annotated]
    metadata = models.JSONField(default=dict)  # snapshot of env vars, resources, config  # type: ignore[var-annotated]
    error_message = models.TextField(blank=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    completed_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    # ── Cloud/object storage tracking ────────────────────────────────────
    cloud_uploaded = models.BooleanField(default=False)  # type: ignore[var-annotated]
    cloud_destination = models.ForeignKey('cloud.CloudStorageDestination', on_delete=models.SET_NULL, null=True, blank=True)  # type: ignore[var-annotated]
    cloud_bucket = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]
    cloud_key = models.CharField(max_length=1024, blank=True, default='',  # type: ignore[var-annotated]
                                 help_text='S3 object key, e.g. smsly-backups/<service>/<filename>')

    class Meta:
        db_table = 'deployments_servicebackup'
        indexes = [
            models.Index(fields=["service", "status"], name="svcbackup_service_status_idx"),
            models.Index(fields=["service", "-created_at"], name="svcbackup_service_created_idx"),
        ]

class ServerBackup(models.Model):
    """Full server export: all services + platform config + Traefik + SSL certs."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)  # type: ignore[var-annotated]
    label = models.CharField(max_length=255, blank=True, default='',  # type: ignore[var-annotated]
                             help_text='Human-readable label for quick identification')
    status = models.CharField(max_length=20, default='PENDING')  # type: ignore[var-annotated]
    db_only = models.BooleanField(default=False)  # type: ignore[var-annotated]
    file_path = models.CharField(max_length=500, blank=True)  # type: ignore[var-annotated]
    size_bytes = models.BigIntegerField(default=0)  # type: ignore[var-annotated]
    services_included = models.JSONField(default=list)  # type: ignore[var-annotated]
    metadata = models.JSONField(default=dict, help_text='Additional metadata: cloud_upload_error, checksums, etc.')  # type: ignore[var-annotated]
    error_message = models.TextField(blank=True, default='')  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    completed_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]
    # ── Cloud/object storage tracking ────────────────────────────────────
    cloud_uploaded = models.BooleanField(default=False)  # type: ignore[var-annotated]
    cloud_destination = models.ForeignKey('cloud.CloudStorageDestination', on_delete=models.SET_NULL, null=True, blank=True)  # type: ignore[var-annotated]
    cloud_bucket = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]
    cloud_key = models.CharField(max_length=1024, blank=True, default='',  # type: ignore[var-annotated]
                                 help_text='S3 object key, e.g. smsly-backups/<scope>/<filename>')

    class Meta:
        db_table = 'deployments_serverbackup'
        indexes = [
            models.Index(fields=["status"], name="srvbackup_status_idx"),
            models.Index(fields=["-created_at"], name="srvbackup_created_idx"),
        ]

class BackupSchedule(models.Model):
    """Cron-based backup schedule per service or server-wide."""
    service = models.ForeignKey('deployments.Service', on_delete=models.CASCADE, null=True, blank=True)  # type: ignore[var-annotated]
    is_server_wide = models.BooleanField(default=False)  # type: ignore[var-annotated]
    db_only = models.BooleanField(default=False)  # type: ignore[var-annotated]
    cron_expression = models.CharField(max_length=100, default='0 3 * * *')  # type: ignore[var-annotated]  # daily 3am
    retention_days = models.IntegerField(default=7)  # type: ignore[var-annotated]
    enabled = models.BooleanField(default=True)  # type: ignore[var-annotated]
    last_run = models.DateTimeField(null=True)  # type: ignore[var-annotated]
    next_run = models.DateTimeField(null=True)  # type: ignore[var-annotated]
    cloud_upload_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text='When True, automatically upload backups to the configured cloud destination. Turn off to keep backups local-only even with credentials set.',
    )
    # ── S3 / object storage destination (optional) ──────────────────────────
    storage_backend = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=[('local', 'Local'), ('s3', 'S3 / R2 / MinIO')],
        default='local',
    )
    s3_bucket = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]
    s3_region = models.CharField(max_length=100, blank=True, default='us-east-1')  # type: ignore[var-annotated]
    s3_endpoint = models.CharField(  # type: ignore[var-annotated]
        max_length=500, blank=True, default='',
        help_text='Custom endpoint for R2/MinIO. Leave blank for AWS S3.',
    )
    s3_access_key = EncryptedCharField(max_length=255, blank=True, default='')
    s3_secret_key = EncryptedCharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'deployments_backupschedule'
        indexes = [
            models.Index(fields=["service", "enabled"], name="bkupsched_service_enabled_idx"),
        ]

    def clean(self):
        super().clean()
        validate_endpoint_url(self.s3_endpoint)


class SnapshotSchedule(models.Model):
    """Cron-based snapshot schedule per service."""
    service = models.ForeignKey('deployments.Service', on_delete=models.CASCADE, related_name='snapshot_schedules', null=True, blank=True)  # type: ignore[var-annotated]
    is_server_wide = models.BooleanField(default=False)  # type: ignore[var-annotated]
    cron_expression = models.CharField(max_length=100, default='0 3 * * *')  # type: ignore[var-annotated]  # daily 3am
    retention_days = models.IntegerField(default=7)  # type: ignore[var-annotated]
    enabled = models.BooleanField(default=True)  # type: ignore[var-annotated]
    last_run = models.DateTimeField(null=True)  # type: ignore[var-annotated]
    next_run = models.DateTimeField(null=True)  # type: ignore[var-annotated]
    cloud_upload_enabled = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text='When True, automatically upload snapshots to the configured cloud destination. Turn off to keep snapshots local-only even with credentials set.',
    )
    # ── S3 / object storage destination (optional) ──────────────────────────
    storage_backend = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=[('local', 'Local'), ('s3', 'S3 / R2 / MinIO')],
        default='local',
    )
    s3_bucket = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]
    s3_region = models.CharField(max_length=100, blank=True, default='us-east-1')  # type: ignore[var-annotated]
    s3_endpoint = models.CharField(  # type: ignore[var-annotated]
        max_length=500, blank=True, default='',
        help_text='Custom endpoint for R2/MinIO. Leave blank for AWS S3.',
    )
    s3_access_key = EncryptedCharField(max_length=255, blank=True, default='')
    s3_secret_key = EncryptedCharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'deployments_snapshotschedule'

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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)  # type: ignore[var-annotated]
    key_id = models.CharField(  # type: ignore[var-annotated]
        max_length=8,
        unique=True,
        db_index=True,
        help_text='4 random bytes as 8-char hex; matches the V2 backup header key_id.',
    )
    fingerprint = models.CharField(  # type: ignore[var-annotated]
        max_length=8,
        db_index=True,
        help_text='First 4 bytes of SHA-256(raw_key) as 8-char hex; matches V2 header fingerprint.',
    )
    key_material_encrypted = EncryptedCharField(
        max_length=512,
        help_text='Fernet BACKUP_ENCRYPTION_KEY, encrypted at rest with FIELD_ENCRYPTION_KEY.',
    )
    label = models.CharField(  # type: ignore[var-annotated]
        max_length=100,
        blank=True,
        default='',
        help_text='Operator label e.g. "master-a-imported-2026-06-14".',
    )
    source = models.CharField(  # type: ignore[var-annotated]
        max_length=20,
        choices=[
            ('AUTO', 'Auto-generated by this master'),
            ('IMPORTED', 'Imported from another master'),
        ],
        default='AUTO',
    )
    is_active = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text='True for the row matching the current BACKUP_ENCRYPTION_KEY env var.',
    )
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    last_used_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    class Meta:
        db_table = 'deployments_backupencryptionkey'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['fingerprint', 'is_active']),
        ]

    def __str__(self):
        return f'BackupEncryptionKey(key_id={self.key_id}, fp={self.fingerprint}, source={self.source})'


class ServiceSnapshot(models.Model):
    """Lightweight, metadata-only point-in-time capture of a service's
    configuration state.  Unlike ``ServiceBackup`` which archives Docker
    images, volumes, and database dumps, a snapshot stores only the JSON
    config payload (env vars, resources, domains, deploy settings, addons)
    making it instant to create and zero-cost in disk space.

    Useful for quick config rollback, deployment diffs, and audit trails.
    """

    class Trigger(models.TextChoices):
        MANUAL = 'MANUAL', 'Manual'
        PRE_DEPLOY = 'PRE_DEPLOY', 'Pre-Deploy'
        PRE_ENV_CHANGE = 'PRE_ENV_CHANGE', 'Pre-Env Change'
        SCHEDULED = 'SCHEDULED', 'Scheduled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)  # type: ignore[var-annotated]
    service = models.ForeignKey(
        'deployments.Service', on_delete=models.CASCADE, related_name='snapshots',
    )  # type: ignore[var-annotated]
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )  # type: ignore[var-annotated]

    label = models.CharField(
        max_length=255, blank=True, default='',
        help_text='Optional human label e.g. "before Redis upgrade"',
    )  # type: ignore[var-annotated]
    trigger = models.CharField(
        max_length=20, choices=Trigger.choices, default=Trigger.MANUAL,
    )  # type: ignore[var-annotated]

    # The full config payload captured at snapshot time.
    config_data = models.JSONField(
        default=dict,
        help_text=(
            'Full config payload: env_vars, deploy_type, docker_image, '
            'repository_url, branch, public_domain, resources, replicas, '
            'addons, build/start commands, health check, etc.'
        ),
    )  # type: ignore[var-annotated]

    # Optional diff chain
    parent_snapshot = models.ForeignKey(
        'self', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='children',
        help_text='Previous snapshot in the diff chain.',
    )  # type: ignore[var-annotated]
    diff_summary = models.JSONField(
        null=True, blank=True, default=None,
        help_text='Computed diff from parent snapshot (added/removed/changed keys).',
    )  # type: ignore[var-annotated]

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    # ── Cloud/object storage tracking ────────────────────────────────────
    cloud_uploaded = models.BooleanField(default=False)  # type: ignore[var-annotated]
    cloud_destination = models.ForeignKey('cloud.CloudStorageDestination', on_delete=models.SET_NULL, null=True, blank=True)  # type: ignore[var-annotated]
    cloud_bucket = models.CharField(max_length=255, blank=True, default='')  # type: ignore[var-annotated]
    cloud_key = models.CharField(max_length=1024, blank=True, default='',  # type: ignore[var-annotated]
                                 help_text='S3 object key, e.g. smsly-snapshots/<service>/<filename>')

    class Meta:
        db_table = 'deployments_servicesnapshot'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['service', '-created_at']),
        ]

    def __str__(self):
        label_part = f' "{self.label}"' if self.label else ''
        return (
            f'ServiceSnapshot({self.id!s}{label_part}, '
            f'trigger={self.trigger}, service={self.service_id})'
        )
