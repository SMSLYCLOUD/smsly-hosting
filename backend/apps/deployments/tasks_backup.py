import logging
import threading

logger = logging.getLogger(__name__)
import os

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

# Serializes backup/restore tasks that carry a per-request encryption key.
# Without this lock, two concurrent Celery workers can clobber each other's
# BACKUP_ENCRYPTION_KEY in os.environ (process-wide).  Tasks that use the
# default key from the environment do NOT acquire the lock.
_backup_key_lock = threading.Lock()  # NOTE: not cooperative with gevent/eventlet pools; assumes prefork workers
from django.utils import timezone

from apps.deployments.models_backup import BackupSchedule
from apps.deployments.services.backup_service import BackupService
from apps.deployments.utils import log_event


@shared_task(bind=True, soft_time_limit=3600, time_limit=3900, max_retries=3, default_retry_delay=300)
def create_service_backup_task(self, service_id, backup_type='MANUAL', backup_id=None, schedule_id=None, encryption_key=None):
    from apps.deployments.utils import log_event

    from .services.backup_service import BackupService
    log_event(
        action='BACKUP_CREATE',
        target=f'Service: {service_id}',
        actor='system',
        metadata={
            'service_id': str(service_id),
            'backup_id': str(backup_id) if backup_id else None,
            'backup_type': backup_type,
            'encryption_key_provided': bool(encryption_key),
        },
    )
    db_only = False
    if schedule_id:
        try:
            sched = BackupSchedule.objects.get(id=schedule_id)
            db_only = sched.db_only
        except BackupSchedule.DoesNotExist:
            pass

    try:
        backup_service = BackupService()
        if encryption_key:
            with _backup_key_lock:
                original_key = os.environ.get('BACKUP_ENCRYPTION_KEY', '')
                os.environ['BACKUP_ENCRYPTION_KEY'] = encryption_key
                try:
                    result = backup_service.backup_service(service_id, backup_id=backup_id, backup_type=backup_type, db_only=db_only)
                finally:
                    if original_key:
                        os.environ['BACKUP_ENCRYPTION_KEY'] = original_key
                    else:
                        os.environ.pop('BACKUP_ENCRYPTION_KEY', None)
        else:
            result = backup_service.backup_service(service_id, backup_id=backup_id, backup_type=backup_type, db_only=db_only)
            
        try:
            from apps.notifications.tasks import notify_backup_completed
            size_mb = (result.size_bytes or 0) / (1024 * 1024)
            notify_backup_completed.delay(result.service.owner.id, str(result.id), size_mb, True)
        except Exception as alert_exc:
            logger.warning("Failed to queue backup success notification: %s", alert_exc)

    except Exception as exc:
        try:
            from apps.deployments.models import Service
            from apps.notifications.tasks import notify_backup_completed
            owner = Service.objects.get(id=service_id).owner
            if owner:
                notify_backup_completed.delay(owner.id, str(backup_id) if backup_id else "unknown", 0.0, False)
        except Exception as alert_exc:
            logger.warning("Failed to queue backup failure notification: %s", alert_exc)

        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise
    finally:
        # Update the schedule's last_run only AFTER the backup completes
        # (success or failure). This prevents the race where last_run was
        # set before the task ran, causing missed schedules on failure.
        if schedule_id:
            _touch_schedule_last_run(schedule_id)


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def verify_backup_integrity_task(self, backup_ids: list | None = None, sample_size: int = 3):
    """Verify backup archive integrity by checking checksums and archive validity.

    When ``backup_ids`` is provided, checks those specific backups. If None,
    samples ``sample_size`` random recent ``COMPLETED`` backups from both
    ``ServiceBackup`` and ``ServerBackup``.

    Checks performed:
      1. File exists and is readable.
      2. SHA-256 checksum matches ``metadata.checksum_sha256`` (if present).
      3. Archive is a valid gzipped tar file (test-open with ``r:gz``).

    Emits an ``AuditLog`` entry per verification run with pass/fail counts.
    """
    import hashlib as _hashlib
    import tarfile as _tarfile

    from apps.deployments.models_audit import AuditLog
    from apps.deployments.models_backup import ServerBackup, ServiceBackup

    candidates = []
    if backup_ids:
        candidates = list(ServiceBackup.objects.filter(
            id__in=backup_ids, status='COMPLETED',
        ))
        candidates += list(ServerBackup.objects.filter(
            id__in=backup_ids, status='COMPLETED',
        ))
    else:
        svc_backups = list(ServiceBackup.objects.filter(
            status='COMPLETED',
        ).order_by('-created_at')[:sample_size])
        srv_backups = list(ServerBackup.objects.filter(
            status='COMPLETED',
        ).order_by('-created_at')[:sample_size])
        candidates = svc_backups + srv_backups

    if not candidates:
        logger.info("verify_backup_integrity: no COMPLETED backups to check.")
        return {'checked': 0, 'passed': 0, 'failed': 0}

    passed = 0
    failed = 0
    results = []

    for backup in candidates:
        filepath = backup.file_path
        try:
            if not filepath or not os.path.exists(filepath):
                raise FileNotFoundError(f"Backup file not found: {filepath}")

            expected_hash = (getattr(backup, 'metadata', None) or {}).get('checksum_sha256', '')
            if expected_hash:
                sha = _hashlib.sha256()
                with open(filepath, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b''):
                        sha.update(chunk)
                if sha.hexdigest() != expected_hash:
                    raise ValueError("Checksum mismatch — backup may be corrupted")

            with _tarfile.open(filepath, 'r:gz') as tar:
                members = tar.getmembers()
                if not members:
                    raise ValueError("Archive is empty")

            passed += 1
            results.append({'id': str(backup.id), 'status': 'passed', 'path': filepath})
        except Exception as exc:
            failed += 1
            results.append({'id': str(backup.id), 'status': 'failed', 'path': filepath, 'error': str(exc)})
            logger.error("Integrity check FAILED for backup %s (%s): %s", backup.id, filepath, exc)

    try:
        AuditLog.objects.create(
            actor='system',
            action='BACKUP_INTEGRITY_CHECK',
            target=f'Checked {len(candidates)} backup(s)',
            metadata={
                'checked': len(candidates),
                'passed': passed,
                'failed': failed,
                'results': results,
            },
        )
    except Exception as exc:
        logger.warning("Failed to record BACKUP_INTEGRITY_CHECK audit log: %s", exc)

    logger.info(
        "Backup integrity check complete: %d/%d passed, %d/%d failed",
        passed, len(candidates), failed, len(candidates),
    )
    return {'checked': len(candidates), 'passed': passed, 'failed': failed, 'results': results}


def _touch_schedule_last_run(schedule_id):
    """Update the BackupSchedule.last_run to now (called after backup completes)."""
    try:
        from django.utils import timezone as tz
        BackupSchedule.objects.filter(id=schedule_id).update(last_run=tz.now())
    except Exception:
        pass


@shared_task(bind=True, soft_time_limit=7200, time_limit=7500, max_retries=2, default_retry_delay=600)
def create_server_backup_task(self, backup_id=None, schedule_id=None, encryption_key=None):
    log_event(
        action='BACKUP_CREATE',
        target='Server',
        actor='system',
        metadata={
            'backup_id': str(backup_id) if backup_id else None,
            'scope': 'server',
            'encryption_key_provided': bool(encryption_key),
        },
    )
    try:
        db_only = False
        if schedule_id:
            from apps.deployments.models_backup import BackupSchedule
            try:
                sched = BackupSchedule.objects.get(id=schedule_id)
                db_only = sched.db_only
            except BackupSchedule.DoesNotExist:
                pass

        backup_service = BackupService()
        if encryption_key:
            with _backup_key_lock:
                original_key = os.environ.get('BACKUP_ENCRYPTION_KEY', '')
                os.environ['BACKUP_ENCRYPTION_KEY'] = encryption_key
                try:
                    backup_service.backup_server(backup_id=backup_id, db_only=db_only)
                finally:
                    if original_key:
                        os.environ['BACKUP_ENCRYPTION_KEY'] = original_key
                    else:
                        os.environ.pop('BACKUP_ENCRYPTION_KEY', None)
        else:
            backup_service.backup_server(backup_id=backup_id, db_only=db_only)
        if schedule_id:
            _touch_schedule_last_run(schedule_id)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=600)
        raise



@shared_task(bind=True, soft_time_limit=3600, time_limit=3900, max_retries=2, default_retry_delay=300)
def restore_service_backup_task(self, backup_id, target_service_id=None, requesting_user_id=None, raise_on_snapshot_failure=True, encryption_key=None):
    log_event(
        action='BACKUP_RESTORE',
        target=f'Backup: {backup_id}',
        actor='system',
        metadata={
            'backup_id': str(backup_id),
            'target_service_id': str(target_service_id) if target_service_id else None,
            'requesting_user_id': str(requesting_user_id) if requesting_user_id else None,
            'scope': 'service',
            'encryption_key_provided': bool(encryption_key),
        },
    )
    backup_service = BackupService()
    # If a key was provided in the request, use it for this restore only
    # (don't persist to BACKUP_ENCRYPTION_KEY env var).
    if encryption_key:
        with _backup_key_lock:
            original_key = os.environ.get('BACKUP_ENCRYPTION_KEY', '')
            os.environ['BACKUP_ENCRYPTION_KEY'] = encryption_key
            try:
                return backup_service.restore_service(
                    backup_id,
                    target_service_id=target_service_id,
                    requesting_user_id=requesting_user_id,
                    raise_on_snapshot_failure=raise_on_snapshot_failure,
                )
            finally:
                # Restore the original env var (or remove if there was none)
                if original_key:
                    os.environ['BACKUP_ENCRYPTION_KEY'] = original_key
                else:
                    os.environ.pop('BACKUP_ENCRYPTION_KEY', None)
    return backup_service.restore_service(
        backup_id,
        target_service_id=target_service_id,
        requesting_user_id=requesting_user_id,
        raise_on_snapshot_failure=raise_on_snapshot_failure,
    )



@shared_task(bind=True, soft_time_limit=7200, time_limit=7500, max_retries=0)
# max_retries=0: destructive operation — no automatic retry on partial restore
def restore_server_backup_task(self, backup_id, requesting_user_id=None, encryption_key=None):
    log_event(
        action='BACKUP_RESTORE',
        target=f'Backup: {backup_id}',
        actor='system',
        metadata={
            'backup_id': str(backup_id),
            'requesting_user_id': str(requesting_user_id) if requesting_user_id else None,
            'scope': 'server',
            'encryption_key_provided': bool(encryption_key),
        },
    )
    backup_service = BackupService()
    if encryption_key:
        with _backup_key_lock:
            original_key = os.environ.get('BACKUP_ENCRYPTION_KEY', '')
            os.environ['BACKUP_ENCRYPTION_KEY'] = encryption_key
            try:
                return backup_service.restore_server(backup_id=backup_id, requesting_user_id=requesting_user_id)
            finally:
                if original_key:
                    os.environ['BACKUP_ENCRYPTION_KEY'] = original_key
                else:
                    os.environ.pop('BACKUP_ENCRYPTION_KEY', None)
    return backup_service.restore_server(backup_id=backup_id, requesting_user_id=requesting_user_id)



@shared_task(bind=True, soft_time_limit=7200, time_limit=7500, max_retries=2, default_retry_delay=120)
def purge_user_backups_task(self, user_id, actor: str = 'system', force: bool = False):
    """
    GDPR right-to-erasure background task.

    Should be enqueued BEFORE the user account is deleted so that the
    ``ServiceBackup.service`` FK still resolves. The task:
      1. Removes ``ServiceBackup.file_path`` tarballs for every service
         owned by the user.
      2. Removes any matching cloud-storage object (S3 / R2 / MinIO).
      3. Deletes the ``ServiceBackup`` and ``ServerBackup`` rows.
      4. Emits an ``AuditLog`` entry recording the count of artifacts purged.
    """
    from apps.deployments.models_audit import AuditLog
    from apps.deployments.services.backup_service import purge_user_backups

    try:
        counters = purge_user_backups(user_id)
    except Exception as exc:
        logger.error("purge_user_backups_task failed for user %s: %s", user_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        if not force:
            raise

    try:
        AuditLog.objects.create(
            actor=actor or 'system',
            action='USER_BACKUPS_PURGED',
            target=f'User: {user_id}',
            metadata={
                'user_id': str(user_id),
                'service_backups_deleted': counters.get('service_backups_deleted', 0),
                'service_backup_files_deleted': counters.get('service_backup_files_deleted', 0),
                'server_backups_deleted': counters.get('server_backups_deleted', 0),
                'server_backup_files_deleted': counters.get('server_backup_files_deleted', 0),
                'cloud_objects_deleted': counters.get('cloud_objects_deleted', 0),
                'errors': counters.get('errors', 0),
            },
        )
    except Exception as exc:
        logger.warning("Failed to record USER_BACKUPS_PURGED audit log: %s", exc)

    return counters



@shared_task(soft_time_limit=600, time_limit=900)
def cleanup_old_backups_task():
    """Delete backups older than retention_days per schedule, including cloud objects."""
    from datetime import timedelta

    from .models_backup import BackupSchedule, ServerBackup, ServiceBackup
    from .services.backup_service import _resolve_cloud_config, delete_cloud_backup_object

    schedules = BackupSchedule.objects.filter(enabled=True)
    cleaned = 0
    for sched in schedules:
        try:
            cutoff = timezone.now() - timedelta(days=sched.retention_days)
            if sched.service:
                old = ServiceBackup.objects.filter(
                    service=sched.service, created_at__lt=cutoff
                ).exclude(backup_type='TRANSFER')
                for b in old:
                    # Delete the cloud object first (idempotent: already-gone = no-op).
                    bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(b)
                    if bucket and key:
                        delete_cloud_backup_object(
                            bucket, key,
                            endpoint=endpoint, region=region,
                            access_key=access_key, secret_key=secret_key,
                        )
                    if b.file_path and os.path.exists(b.file_path):
                        os.remove(b.file_path)
                    b.delete()
                    cleaned += 1
            elif sched.is_server_wide:
                old = ServerBackup.objects.filter(created_at__lt=cutoff)
                for b in old:
                    bucket, key, endpoint, region, access_key, secret_key = _resolve_cloud_config(b)
                    if bucket and key:
                        delete_cloud_backup_object(
                            bucket, key,
                            endpoint=endpoint, region=region,
                            access_key=access_key, secret_key=secret_key,
                        )
                    if b.file_path and os.path.exists(b.file_path):
                        os.remove(b.file_path)
                    b.delete()
                    cleaned += 1
        except Exception as exc:
            logger.warning("Backup cleanup failed for schedule %s: %s", sched.id, exc)

    from .models_backup import ServiceSnapshot, SnapshotSchedule
    snapshot_schedules = SnapshotSchedule.objects.filter(enabled=True)
    for sched in snapshot_schedules:
        try:
            cutoff = timezone.now() - timedelta(days=sched.retention_days)
            if sched.service:
                old = ServiceSnapshot.objects.filter(service=sched.service, created_at__lt=cutoff)
                for snap in old:
                    # Clean up DB clone if one exists
                    db_clone = (snap.config_data or {}).get('_db_clone')
                    if db_clone:
                        try:
                            from apps.deployments.services.snapshot_service import SnapshotService
                            SnapshotService.cleanup_db_clone(db_clone)
                        except Exception as clone_exc:
                            logger.warning(
                                "Failed to clean up DB clone %s for snapshot %s: %s",
                                db_clone, snap.id, clone_exc,
                            )

                    if snap.cloud_uploaded and snap.cloud_key:
                        # Optional: delete from cloud storage if needed, but not implemented for snapshots yet
                        pass
                    snap.delete()
                    cleaned += 1
        except Exception as exc:
            logger.warning("Snapshot cleanup failed for schedule %s: %s", sched.id, exc)

    return cleaned



def _make_aware(dt):
    """Convert a timezone-naive datetime to aware using the current timezone.

    croniter.get_prev() / get_next() return naive datetimes, but Django's
    ORM returns aware datetimes (USE_TZ=True).  Comparing them raises::

        TypeError: can't compare offset-naive and offset-aware datetimes

    This helper makes croniter's output aware so the comparison works.
    """
    if dt is not None and timezone.is_naive(dt):
        return timezone.make_aware(dt)
    return dt


@shared_task(soft_time_limit=3600, time_limit=3900)
def run_scheduled_backups_task():
    """Execute all due BackupSchedule entries."""
    import croniter  # type: ignore[import-untyped]

    from .models_backup import BackupSchedule

    now = timezone.now()
    schedules = BackupSchedule.objects.filter(enabled=True)
    ran = 0
    for sched in schedules:
        try:
            cron = croniter.croniter(sched.cron_expression, now)
            prev_run = _make_aware(cron.get_prev(timezone.datetime))
            if sched.last_run and sched.last_run >= prev_run:
                continue
            # Compute next_run now but defer last_run — the Celery task
            # updates it AFTER the backup completes to prevent the race
            # where a failed task leaves the schedule thinking it ran.
            cron = croniter.croniter(sched.cron_expression, now)
            next_dt = _make_aware(cron.get_next(timezone.datetime))
            sched.next_run = next_dt
            sched.save(update_fields=['next_run'])

            if sched.is_server_wide:
                create_server_backup_task.delay(schedule_id=sched.id)
            elif sched.service:
                create_service_backup_task.delay(str(sched.service.id), backup_type='SCHEDULED', schedule_id=sched.id)
            ran += 1
        except Exception as exc:
            logger.exception("Scheduled backup failed for schedule %s: %s", sched.id, exc)
    return ran


@shared_task(soft_time_limit=3600, time_limit=3900)
def run_scheduled_snapshots_task():
    """Execute all due SnapshotSchedule entries."""
    import croniter  # type: ignore[import-untyped]

    from .models_backup import SnapshotSchedule

    now = timezone.now()
    schedules = SnapshotSchedule.objects.filter(enabled=True)
    ran = 0
    for sched in schedules:
        try:
            cron = croniter.croniter(sched.cron_expression, now)
            prev_run = _make_aware(cron.get_prev(timezone.datetime))
            if sched.last_run and sched.last_run >= prev_run:
                continue

            cron = croniter.croniter(sched.cron_expression, now)
            next_dt = _make_aware(cron.get_next(timezone.datetime))
            sched.next_run = next_dt
            sched.save(update_fields=['next_run'])

            if sched.service:
                # Currently snapshots are only per-service
                create_snapshot_task.delay(
                    service_id=str(sched.service.id),
                    trigger='SCHEDULED',
                    label='Automated Snapshot',
                )
            ran += 1
        except Exception as exc:
            logger.exception("Scheduled snapshot failed for schedule %s: %s", sched.id, exc)
    return ran


@shared_task(bind=True, soft_time_limit=300, time_limit=360, max_retries=2, default_retry_delay=60)
def create_snapshot_task(self, service_id: str, trigger: str = 'MANUAL', label: str = '', created_by_id: str | None = None):
    from django.contrib.auth import get_user_model

    from apps.deployments.services.snapshot_service import SnapshotService

    User = get_user_model()
    created_by = None
    if created_by_id:
        try:
            created_by = User.objects.get(id=created_by_id)
        except User.DoesNotExist:
            pass

    try:
        SnapshotService.capture_snapshot(
            service_id=service_id,
            trigger=trigger,
            label=label,
            created_by=created_by,
        )
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise

