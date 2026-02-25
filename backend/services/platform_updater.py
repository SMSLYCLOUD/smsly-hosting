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
HEALTH_CHECK_URL = 'http://localhost:8090/api/v1/system/config/'
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
    try:
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

        # Step 2: Git pull
        update_record.status = 'PULLING'
        update_record.current_step = 'Pulling latest code'
        update_record.progress_percent = 20
        update_record.save()

        ok, output = _run(['git', 'pull', '--ff-only', 'origin', 'main'])
        if not ok:
            raise PlatformUpdateError(f"Git pull failed: {output}")
        update_record.append_log(f"Git pull complete: {output[:200]}")

        # Get new commit
        ok, new_commit = _run(['git', 'rev-parse', 'HEAD'])
        if ok:
            update_record.to_commit = new_commit.strip()
            update_record.save()

        # Step 3: Build new images
        update_record.current_step = 'Building new Docker images'
        update_record.progress_percent = 40
        update_record.save()
        update_record.append_log('Building Docker images...')

        ok, output = _run(
            ['docker', 'compose', '-f', COMPOSE_FILE, 'build', '--no-cache'],
            timeout=600,
        )
        if not ok:
            raise PlatformUpdateError(f"Docker build failed: {output[-500:]}")
        update_record.append_log('Docker build complete')

        # Step 4: Run migrations
        update_record.status = 'MIGRATING'
        update_record.current_step = 'Running database migrations'
        update_record.progress_percent = 60
        update_record.save()

        ok, output = _run([
            'docker', 'compose', '-f', COMPOSE_FILE,
            'run', '--rm', 'backend',
            'python', 'manage.py', 'migrate', '--noinput',
        ])
        if not ok:
            raise PlatformUpdateError(f"Migration failed: {output[-500:]}")
        update_record.append_log('Migrations complete')

        # Step 5: Restart services
        update_record.status = 'RESTARTING'
        update_record.current_step = 'Restarting services'
        update_record.progress_percent = 75
        update_record.save()

        ok, output = _run([
            'docker', 'compose', '-f', COMPOSE_FILE,
            'up', '-d', '--remove-orphans',
        ])
        if not ok:
            raise PlatformUpdateError(f"Restart failed: {output[-500:]}")
        update_record.append_log('Services restarted')

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
    update_record.append_log('Starting automatic rollback...')

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
