"""Cloud storage upload/download operations for backups."""

import logging
import os
from typing import Any

from .s3 import delete_cloud_backup_object, download_from_s3, upload_backup_to_s3

logger = logging.getLogger(__name__)


def _resolve_cloud_config(backup):
    """Resolve cloud bucket + key + credentials for a backup record.

    Priority: 1) stored cloud fields on backup, 2) BackupSchedule lookup.
    Returns (bucket, key, endpoint, region, access_key, secret_key) or
    (None, None, None, None, None, None) if nothing found.
    """
    bucket = getattr(backup, 'cloud_bucket', '') or ''
    key = getattr(backup, 'cloud_key', '') or ''
    if bucket and key:
        dest = getattr(backup, 'cloud_destination', None)
        if dest:
            return bucket, key, dest.endpoint, dest.region, dest.access_key, dest.secret_key
        return bucket, key, '', 'us-east-1', '', ''
    from apps.cloud.models.backup import BackupSchedule
    service_id = getattr(backup, 'service_id', None)
    if service_id:
        sched = BackupSchedule.objects.filter(
            service_id=service_id, enabled=True, storage_backend='s3',
        ).first()
        if sched and sched.s3_bucket and sched.s3_access_key:
            service_name = getattr(getattr(backup, 'service', None), 'name', 'unknown')
            derived_key = f"smsly-backups/{service_name}/{os.path.basename(backup.file_path or 'unknown')}"
            return (sched.s3_bucket, derived_key, sched.s3_endpoint,
                    sched.s3_region, sched.s3_access_key, sched.s3_secret_key)
    else:
        sched = BackupSchedule.objects.filter(
            is_server_wide=True, enabled=True, storage_backend='s3',
        ).first()
        if sched and sched.s3_bucket and sched.s3_access_key:
            derived_key = f"smsly-backups/server/{os.path.basename(backup.file_path or 'unknown')}"
            return (sched.s3_bucket, derived_key, sched.s3_endpoint,
                    sched.s3_region, sched.s3_access_key, sched.s3_secret_key)
    return None, None, None, None, None, None


def _upload_backup_to_cloud(backup, filepath, service_name):
    """Upload a backup to cloud storage and track metadata on the backup record."""
    result: dict[str, Any] = {"uploaded": False, "reason": "", "bucket": "", "key": ""}
    try:
        from apps.cloud.models.backup import BackupSchedule
        service_id = getattr(backup, 'service_id', None)
        dest = getattr(backup, 'cloud_destination', None)

        if service_id:
            sched = BackupSchedule.objects.filter(
                service_id=service_id, enabled=True,
            ).first()
        else:
            sched = BackupSchedule.objects.filter(
                is_server_wide=True, enabled=True,
            ).first()

        if sched is not None and not sched.cloud_upload_enabled:
            result["reason"] = "cloud_upload_enabled=False on schedule"
            logger.info(
                "Cloud upload skipped for %s: %s",
                service_name, result["reason"],
            )
            return result

        if dest:
            s3_bucket = dest.bucket
            s3_endpoint = dest.endpoint
            s3_region = dest.region
            s3_access_key = dest.access_key
            s3_secret_key = dest.secret_key
        else:
            if not sched or sched.storage_backend != 's3' or not sched.s3_bucket or not sched.s3_access_key:
                result["reason"] = "No S3 destination configured"
                return result
            s3_bucket = sched.s3_bucket
            s3_endpoint = sched.s3_endpoint
            s3_region = sched.s3_region
            s3_access_key = sched.s3_access_key
            s3_secret_key = sched.s3_secret_key

        from .exceptions import _DEFAULT_MAX_BACKUP_SIZE
        try:
            max_bytes = int(os.environ.get("BACKUP_MAX_SIZE_BYTES", str(_DEFAULT_MAX_BACKUP_SIZE)))
        except (TypeError, ValueError):
            max_bytes = _DEFAULT_MAX_BACKUP_SIZE
        file_size = os.path.getsize(filepath)
        if max_bytes > 0 and file_size > max_bytes:
            result["reason"] = f"Backup size ({file_size} bytes) exceeds BACKUP_MAX_SIZE_BYTES ({max_bytes} bytes)"
            logger.warning("Skipping S3 upload for %s: %s", service_name, result["reason"])
            return result

        result["bucket"] = s3_bucket
        result["key"] = f"smsly-backups/{service_name}/{os.path.basename(filepath)}"
        backup_id_str = str(getattr(backup, 'id', ''))
        class _S3UploadProgress:
            def __init__(self):
                self.total = os.path.getsize(filepath)
                self.transferred = 0
            def __call__(self, bytes_amount):
                self.transferred += bytes_amount
                pct = min(95, (self.transferred / max(self.total, 1)) * 100)
                from .core import BackupService
                BackupService._broadcast_progress(
                    backup_id_str, 'cloud_upload', percent=pct,
                    message=f'Uploading... {self.transferred // (1024 * 1024)} MB',
                    bytes_transferred=self.transferred, total_bytes=self.total,
                )
        progress = _S3UploadProgress()
        ok = upload_backup_to_s3(
            filepath, s3_bucket, result["key"],
            endpoint=s3_endpoint, region=s3_region,
            access_key=s3_access_key, secret_key=s3_secret_key,
            progress_callback=progress,
        )
        if ok:
            backup.cloud_uploaded = True
            backup.cloud_bucket = s3_bucket
            backup.cloud_key = result["key"]
            backup.save(update_fields=['cloud_uploaded', 'cloud_bucket', 'cloud_key'])
            result["uploaded"] = True
        else:
            result["reason"] = "S3 upload returned failure — check credentials, network, or bucket permissions"
        return result
    except Exception as exc:
        result["reason"] = str(exc)
        logger.warning("Cloud upload skipped for %s: %s", service_name, exc)
    return result


def _alert_cloud_upload_failed(backup, cloud_result: dict):
    """Log audit event and create in-app notification when cloud upload fails."""
    try:
        from django.utils import timezone as tz

        backup_type = "server" if getattr(backup, 'services_included', None) is not None else "service"
        service_id = getattr(backup, 'service_id', None)
        backup_id = str(getattr(backup, 'id', ''))
        bucket = cloud_result.get('bucket', '')
        key = cloud_result.get('key', '')
        reason = cloud_result.get('reason', 'unknown')

        from apps.deployments.utils import log_event
        log_event(
            action='BACKUP_CLOUD_UPLOAD_FAILED',
            target=f'{backup_type.capitalize()} backup {backup_id}',
            actor='system',
            metadata={
                'backup_id': backup_id,
                'backup_type': backup_type,
                'service_id': str(service_id) if service_id else None,
                'bucket': bucket,
                'key': key,
                'reason': reason,
                'timestamp': tz.now().isoformat(),
            },
        )

        from apps.deployments.models import Service
        if service_id:
            try:
                svc = Service.objects.select_related('owner').only(
                    'name', 'owner',
                ).get(id=service_id)
            except Service.DoesNotExist:
                svc = None
            if svc and svc.owner:
                try:
                    from apps.notifications.models import Notification
                    Notification.objects.create(
                        user=svc.owner,
                        title='Cloud backup upload failed',
                        message=(
                            f"Backup of '{svc.name}' completed locally but "
                            f"could not be uploaded to cloud storage "
                            f"({cloud_result.get('bucket', 'S3')}). "
                            f"Reason: {reason}. The backup is safe on the "
                            f"local server."
                        ),
                        event_type='backup_cloud_failed',
                    )
                except Exception as exc:
                    logger.warning("Failed to send backup cloud failure notification to user: %s", exc)

            try:
                from apps.core.tasks.alerts import (
                    _send_alerts_for_backup_cloud_failure,
                )
                _send_alerts_for_backup_cloud_failure.delay(
                    service_id=str(service_id),
                    backup_id=backup_id,
                    reason=str(reason),
                    bucket=str(bucket),
                    key=str(key),
                )
            except Exception as exc:
                logger.warning("Failed to dispatch backup cloud failure alert task: %s", exc)

    except Exception as exc:
        logger.warning("Failed to create cloud upload alert: %s", exc)


def _download_backup_from_cloud(backup, local_path) -> bool:
    """Download a backup from cloud storage to local path.

    Returns True on success.
    """
    bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(backup)
    if not bucket or not key:
        logger.warning("No cloud config found to download backup %s", backup.id)
        return False
    backup_id_str = str(getattr(backup, 'id', ''))
    total_size = getattr(backup, 'size_bytes', 0) or 0
    class _S3DownloadProgress:
        def __init__(self):
            self.transferred = 0
        def __call__(self, bytes_amount):
            self.transferred += bytes_amount
            from .core import BackupService
            pct = min(95, (self.transferred / max(total_size, 1)) * 100) if total_size else 0
            BackupService._broadcast_progress(
                backup_id_str, 'downloading', percent=pct,
                message=f'Downloading from cloud... {self.transferred // (1024 * 1024)} MB',
                bytes_transferred=self.transferred, total_bytes=total_size,
            )
    return download_from_s3(
        bucket, key, local_path,
        endpoint=endpoint, region=region,
        access_key=access_key, secret_key=secret_key,
        progress_callback=_S3DownloadProgress(),
    )


def _delete_backup_cloud_object(backup) -> bool:
    """Delete a backup's cloud object (S3/R2/MinIO).

    Returns True on success or if no cloud config exists.
    """
    bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(backup)
    if not bucket or not key:
        return True
    ok = delete_cloud_backup_object(
        bucket, key,
        endpoint=endpoint, region=region,
        access_key=access_key, secret_key=secret_key,
    )
    if ok:
        logger.info("Deleted cloud object s3://%s/%s for backup %s", bucket, key, backup.id)
    else:
        logger.warning("Failed to delete cloud object s3://%s/%s for backup %s", bucket, key, backup.id)
    return ok
