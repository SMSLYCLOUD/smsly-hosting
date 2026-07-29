"""Cloud storage destinations for backup offloading — R2, S3, MinIO, B2."""
import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

from .backup import validate_endpoint_url


class CloudStorageDestination(models.Model):
    """Pre-configured cloud storage target for backup offloading.

    When `service` is NULL → platform-wide destination (visible everywhere).
    When `service` is set → per-service destination (only for that service).
    """

    TEMPLATES = {
        'r2': {
            'name': 'Cloudflare R2',
            'endpoint': 'https://{account_id}.r2.cloudflarestorage.com',
            'region': 'auto',
        },
        's3': {
            'name': 'Amazon S3',
            'endpoint': '',
            'region': 'us-east-1',
        },
        'minio': {
            'name': 'MinIO / Self-Hosted S3',
            'endpoint': 'https://your-minio-server:9000',
            'region': 'us-east-1',
        },
        'b2': {
            'name': 'Backblaze B2 (S3-compatible)',
            'endpoint': 'https://s3.us-west-004.backblazeb2.com',
            'region': 'us-west-004',
        },
        'digitalocean': {
            'name': 'DigitalOcean Spaces',
            'endpoint': 'https://nyc3.digitaloceanspaces.com',
            'region': 'nyc3',
        },
        'vps': {
            'name': 'Custom Storage VPS',
            'endpoint': 'https://your-vps-ip:9000',
            'region': 'us-east-1',
        },
        'wasabi': {
            'name': 'Wasabi Hot Storage',
            'endpoint': 'https://s3.wasabisys.com',
            'region': 'us-east-1',
        },
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)  # type: ignore[var-annotated]
    name = models.CharField(max_length=200)  # type: ignore[var-annotated]
    provider = models.CharField(  # type: ignore[var-annotated]
        max_length=30,
        choices=[(k, v['name']) for k, v in TEMPLATES.items()],
        default='s3',
    )
    bucket = models.CharField(max_length=255)  # type: ignore[var-annotated]
    region = models.CharField(max_length=100, default='us-east-1')  # type: ignore[var-annotated]
    endpoint = models.CharField(  # type: ignore[var-annotated]
        max_length=500, blank=True, default='',
        help_text='Custom endpoint for R2/MinIO/B2. Leave blank for AWS S3.',
    )
    # max_length=500: Fernet encryption adds ~120 bytes of base64url overhead
    # on top of the plaintext credential. Old max_length=255 caused 400 Bad
    # Request in production when storing longer S3/R2/MinIO keys (>64 chars).
    access_key = EncryptedCharField(max_length=500, blank=False)
    secret_key = EncryptedCharField(max_length=500, blank=False)
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    service = models.ForeignKey(  # type: ignore[var-annotated]
        'deployments.Service', on_delete=models.CASCADE, null=True, blank=True,
        related_name='cloud_destinations',
        help_text='NULL = platform-wide; set = per-service only',
    )

    class Meta:
        db_table = 'deployments_cloudstoragedestination'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_provider_display()})"

    def apply_to_schedule(self, schedule):
        """Copy S3 config from this destination to a BackupSchedule."""
        schedule.storage_backend = 's3'
        schedule.s3_bucket = self.bucket
        schedule.s3_region = self.region
        schedule.s3_endpoint = self.endpoint
        schedule.s3_access_key = self.access_key
        schedule.s3_secret_key = self.secret_key
        schedule.save(update_fields=[
            'storage_backend', 's3_bucket', 's3_region',
            's3_endpoint', 's3_access_key', 's3_secret_key',
        ])

    def clean(self):
        super().clean()
        validate_endpoint_url(self.endpoint)

    def upload_test_file(self) -> bool:
        """Upload a test file to verify connectivity."""
        import os
        import tempfile

        from apps.cloud.services.backup_service import upload_backup_to_s3
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('SMSLY connectivity test')
            path = f.name
        try:
            return upload_backup_to_s3(
                path, self.bucket, '.smsly-test-file',
                endpoint=self.endpoint, region=self.region,
                access_key=self.access_key, secret_key=self.secret_key,
            )
        finally:
            os.unlink(path)
