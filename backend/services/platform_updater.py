"""
Resilient platform self-updater.

Update flow:
  1. Snapshot current state (image tags, container IDs)
  2. Git pull latest code
  3. Build new images
  4. Run Django migrations (with backup)
  5. Blue-green restart: start new containers, verify health, stop old
  6. If health check fails → automatic rollback

Rollback:
  - Revert to snapshot image tags
  - Re-tag and restart old containers
  - Revert migrations if possible
"""
import os
import subprocess
import logging
import time
from datetime import timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)


class PlatformUpdateError(Exception):
    """Raised when platform update fails."""


INSTALL_DIR = os.environ.get('INSTALL_DIR', '/opt/smsly-hosting')
COMPOSE_FILE = os.path.join(INSTALL_DIR, 'docker-compose.prod.yml')
# Use a public liveness endpoint for post-update validation. Admin-only API
# routes return 401/403 and can cause false rollback failures.
HEALTH_CHECK_URL = os.environ.get('PLATFORM_HEALTH_CHECK_URL', 'http://localhost:8090/health')
HEALTH_CHECK_RETRIES = 10
HEALTH_CHECK_INTERVAL = 5  # seconds


def _run(cmd: list, cwd: str = INSTALL_DIR, timeout: int = 300) -> tuple:
    """Run a command, return (success, output)."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return False, str(e)


def snapshot_current_state() -> dict:
    """Capture current container image tags for rollback."""
    ok, output = _run(['docker', 'compose', '-f', COMPOSE_FILE, 'ps', '--format', 'json'])
    if not ok:
        return {}

    # Get image tags for each service
    ok, output = _run(['docker', 'compose', '-f', COMPOSE_FILE, 'config', '--images'])
    images = {}
    if ok:
        for line in output.strip().split('\n'):
            if line.strip():
                images[line.strip()] = True  # Store image names

    # Get current git commit
    ok, commit = _run(['git', 'rev-parse', 'HEAD'])

    return {
        'images': images,
        'commit': commit.strip() if ok else '',
        'timestamp': timezone.now().isoformat(),
    }


def check_health() -> bool:
    """Check if platform is healthy after update."""
    import urllib.request
    for attempt in range(HEALTH_CHECK_RETRIES):
        try:
            req = urllib.request.urlopen(HEALTH_CHECK_URL, timeout=5)
            if req.status == 200:
                return True
        except Exception:
            pass
        time.sleep(HEALTH_CHECK_INTERVAL)
    return False


def perform_update(update_record) -> bool:
    """
    Execute platform update with rollback protection.

    Args:
        update_record: PlatformUpdate model instance

    Returns:
        True if update succeeded, False if failed/rolled back
    """
    from apps.deployments.models_updates import PlatformUpdate

    from apps.core.services.audit_service import AuditService
    from django.db import transaction
    import os

    with transaction.atomic():
        # Lock the row if it exists or use a global lock check
        active_updates = PlatformUpdate.objects.select_for_update().filter(
            status__in=[
                'QUEUED',
                'PREFLIGHT_RUNNING',
                'SNAPSHOTTING',
                'UPDATING',
                'MIGRATING',
                'HEALTH_CHECKING',
                'ROLLBACK_STARTED',
                'ROLLBACK_RUNNING',
                'PENDING', 'PULLING', 'BACKING_UP', 'RESTARTING', 'HEALTH_CHECK'
            ]
        ).exclude(id=update_record.id).exists()

        if active_updates:
            update_record.error_message = 'Another update is currently in progress'
            update_record.status = 'FAILED'
            update_record.save()
            return False

    try:
        update_record.status = 'PREFLIGHT_RUNNING'
        update_record.save()
        AuditService.log("paas_update_started", target=f"update_{update_record.id}")

        db_url = os.getenv("DIRECT_DATABASE_URL")
        if not db_url:
            update_record.append_log("DIRECT_DATABASE_URL is missing. Aborting to protect database integrity.")
            raise PlatformUpdateError("DIRECT_DATABASE_URL missing")

        # Check if manage.py is in root or backend/
        manage_py_path = "manage.py"
        if not os.path.exists(os.path.join(INSTALL_DIR, manage_py_path)):
            manage_py_path = "backend/manage.py"

        ok, migrations_out = _run(["python", manage_py_path, "showmigrations", "--plan"])
        if ok:
            update_record.append_log("Migration state recorded.")
        else:
            update_record.append_log(f"Failed to record migrations: {migrations_out}")
            raise PlatformUpdateError("Migration recording failed")

        update_record.status = 'SNAPSHOTTING'
        update_record.save()

        if str(os.getenv("PAAS_ENABLE_DB_SNAPSHOTS", "true")).lower() == "true":
            backup_dir = os.getenv("PAAS_BACKUP_DIR", "/var/lib/cloudneuron/backups")
            os.makedirs(backup_dir, exist_ok=True)
            import datetime
            ts = datetime.datetime.now().strftime("%Y%md%H%M%S")
            snapshot_path = os.path.join(backup_dir, f"db_snapshot_{update_record.id}_{ts}.dump")

            update_record.append_log(f"Creating snapshot at {snapshot_path}")

            ok, snap_out = _run(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", snapshot_path, db_url])
            if not ok:
                 update_record.append_log(f"Snapshot failed: {snap_out}")
                 raise PlatformUpdateError("Snapshot failed")

            # In old model, snapshot_data is JSON.
            sd = update_record.snapshot_data or {}
            sd['db_snapshot_path'] = snapshot_path
            update_record.snapshot_data = sd
            update_record.save(update_fields=['snapshot_data'])
            AuditService.log("paas_update_snapshot_created", target=f"update_{update_record.id}")
        else:
            sd = update_record.snapshot_data or {}
            sd['db_snapshot_path'] = "skipped"
            update_record.snapshot_data = sd
            update_record.save(update_fields=['snapshot_data'])
        # Step 1: Snapshot
        update_record.status = 'BACKING_UP'
        update_record.current_step = 'Creating pre-update snapshot'
        update_record.progress_percent = 10
        update_record.save()
        update_record.append_log('Creating snapshot of current state...')

        snapshot = snapshot_current_state()
        update_record.snapshot_data = snapshot
        update_record.from_commit = snapshot.get('commit', '')
        update_record.save()
        update_record.append_log(f"Snapshot created: commit={snapshot.get('commit', 'unknown')}")

        # Step 5: Execute full platform update via install.sh inside a monitorable screen session
        # This allows the user to monitor progress via 'screen -r UI-update' while the UI tracks status.
        update_record.status = 'UPDATING'
        update_record.current_step = 'Executing update via screen (UI-update)'
        update_record.progress_percent = 50
        update_record.save()
        
        update_cmd = [
            'screen', '-dmS', 'UI-update',
            'bash', os.path.join(INSTALL_DIR, 'install.sh'), '--update', '--no-screen'
        ]
        
        update_record.append_log(f"Triggering background update in screen session 'UI-update'...")
        ok, output = _run(update_cmd)
        
        if not ok:
            raise PlatformUpdateError(f"Failed to start screen session: {output}")
            
        update_record.append_log("Update session started. Monitor via: screen -r UI-update")
        
        # We now wait for the screen session to finish or timeout
        # Since 'install.sh --update' can take minutes, we poll for the session's existence.
        max_wait = 1800 # 30 minutes
        waited = 0
        while waited < max_wait:
            time.sleep(10)
            waited += 10
            
            # Check if screen session is still alive
            check_ok, _ = _run(['screen', '-ls', 'UI-update'])
            if not check_ok:
                # Session ended (could be success or failure)
                break
            
            # Update progress based on elapsed time (fake but indicative)
            if update_record.progress_percent < 90:
                update_record.progress_percent += 1
                update_record.save()

        update_record.append_log('Update session finished or detached.')
        update_record.progress_percent = 90
        update_record.save()

        # Step 6: Health check
        update_record.status = 'HEALTH_CHECK'
        update_record.current_step = 'Verifying platform health'
        update_record.progress_percent = 90
        update_record.save()

        if not check_health():
            raise PlatformUpdateError('Health check failed after update')
        update_record.append_log('Health check passed!')

        # Success
        update_record.status = 'COMPLETED'
        update_record.current_step = 'Update completed successfully'
        update_record.progress_percent = 100
        update_record.completed_at = timezone.now()
        update_record.rollback_deadline = timezone.now() + timedelta(hours=1)
        update_record.save()
        update_record.append_log('✓ Update completed successfully')
        return True

    except Exception as e:
        error_msg = str(e)
        update_record.append_log(f'✗ Update failed: {error_msg}')
        update_record.error_message = error_msg
        update_record.save()

        # Automatic rollback
        return _rollback(update_record)


def _rollback(update_record) -> bool:
    """Roll back to the snapshot state."""
    from apps.core.services.audit_service import AuditService
    import os

    update_record.status = 'ROLLBACK_RUNNING'
    update_record.save()
    AuditService.log("paas_rollback_started", target=f"update_{update_record.id}")

    allow_restore = str(os.getenv("PAAS_ALLOW_AUTOMATED_DB_RESTORE", "false")).lower() == "true"
    db_snap = update_record.snapshot_data.get('db_snapshot_path') if isinstance(update_record.snapshot_data, dict) else None
    if allow_restore and db_snap and db_snap != "skipped":
         update_record.append_log("Restoring database from snapshot...")

         db_url = os.getenv("DIRECT_DATABASE_URL")
         if not db_url:
             update_record.append_log("DIRECT_DATABASE_URL missing during restore. Failing rollback.")
             update_record.status = 'FAILED'
             update_record.save()
             return False

         env = os.getenv("PAAS_ENVIRONMENT", "production")
         if env != "production" and not os.getenv("PAAS_ALLOW_DANGEROUS_RESTORE"):
             update_record.append_log("Cannot restore to non-production database without explicit override.")
             update_record.status = 'FAILED'
             update_record.save()
             return False

         AuditService.log("paas_rollback_db_restore_started", target=f"update_{update_record.id}")

         ok, res_out = _run(["pg_restore", "--clean", "--no-owner", "--no-acl", "--if-exists", "-d", db_url, db_snap])
         if not ok:
             update_record.append_log(f"Restore failed: {res_out}")
             update_record.status = 'FAILED'
             update_record.save()
             return False

         AuditService.log("paas_rollback_db_restore_succeeded", target=f"update_{update_record.id}")

    elif not allow_restore and db_snap and db_snap != "skipped":
         update_record.append_log("Automated DB restore disabled. Manual DB restore required.")
         update_record.status = 'FAILED'
         update_record.error_message = f"Rollback partial: Manual DB restore required. Check snapshot: {db_snap}"
         update_record.save()
         AuditService.log("paas_rollback_failed", target=f"update_{update_record.id}", status="failed", message="Manual DB restore required")
         return False

    update_record.append_log('Starting automatic rollback of application containers...')

    snapshot = update_record.snapshot_data
    old_commit = snapshot.get('commit', '')

    if old_commit:
        ok, output = _run(['git', 'checkout', old_commit])
        update_record.append_log(
            f"Git rollback: {'OK' if ok else 'FAILED'}")

    # Rebuild with old code
    ok, output = _run(
        ['docker', 'compose', '-f', COMPOSE_FILE, 'build'],
        timeout=600,
    )
    update_record.append_log(
        f"Rebuild old images: {'OK' if ok else 'FAILED'}")

    # Restart old containers
    ok, output = _run([
        'docker', 'compose', '-f', COMPOSE_FILE,
        'up', '-d', '--remove-orphans',
    ])
    update_record.append_log(
        f"Restart old containers: {'OK' if ok else 'FAILED'}")

    if check_health():
        update_record.status = 'ROLLED_BACK'
        update_record.append_log('✓ Rollback successful, platform is healthy')
    else:
        update_record.status = 'FAILED'
        update_record.append_log('✗ Rollback failed — manual intervention required')

    update_record.can_rollback = False
    update_record.completed_at = timezone.now()
    update_record.save()
    return False
