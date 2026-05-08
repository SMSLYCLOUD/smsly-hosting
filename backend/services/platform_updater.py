"""
Resilient platform self-updater.

The application containers cannot safely update the host checkout or restart the
compose stack themselves: the backend/Celery containers run without systemd and
are often restarted by the update they trigger.  The UI therefore records a
PlatformUpdate row, drops an update request into the shared host bind mount, and
lets the host-level smsly-update-watcher systemd service run install.sh.
"""
import logging
import os
import subprocess
import time
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

logger = logging.getLogger(__name__)


class PlatformUpdateError(Exception):
    """Raised when platform update fails."""


INSTALL_DIR = os.environ.get('INSTALL_DIR', '/opt/smsly-hosting')
COMPOSE_FILE = os.path.join(INSTALL_DIR, 'docker-compose.prod.yml')
# Shared bind mount used by caddy-watcher and smsly-update-watcher.  Inside the
# containers it is mounted at /caddy-config; on the host it is
# /opt/smsly-hosting/caddy-config.
UPDATE_WATCH_DIR = Path(os.environ.get('PLATFORM_UPDATE_WATCH_DIR', '/caddy-config'))
UPDATE_FLAG = UPDATE_WATCH_DIR / '.update'
UPDATE_STATUS = UPDATE_WATCH_DIR / '.update.status'
HEALTH_CHECK_URL = os.environ.get('PLATFORM_HEALTH_CHECK_URL', 'http://localhost:8090/health')
HEALTH_CHECK_RETRIES = int(os.environ.get('PLATFORM_HEALTH_CHECK_RETRIES', '10'))
HEALTH_CHECK_INTERVAL = int(os.environ.get('PLATFORM_HEALTH_CHECK_INTERVAL', '5'))
UPDATE_WATCHER_TIMEOUT = int(os.environ.get('PLATFORM_UPDATE_WATCHER_TIMEOUT', '3600'))


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
    """Capture current container image tags for rollback/logging when available."""
    ok, output = _run(['docker', 'compose', '-f', COMPOSE_FILE, 'config', '--images'])
    images = {}
    if ok:
        for line in output.strip().split('\n'):
            if line.strip():
                images[line.strip()] = True

    ok, commit = _run(['git', 'rev-parse', 'HEAD'])

    return {
        'images': images,
        'commit': commit.strip() if ok else '',
        'timestamp': timezone.now().isoformat(),
    }


def check_health() -> bool:
    """Check if platform is healthy after update."""
    import urllib.request
    for _attempt in range(HEALTH_CHECK_RETRIES):
        try:
            req = urllib.request.urlopen(HEALTH_CHECK_URL, timeout=5)
            if req.status == 200:
                return True
        except Exception:
            pass
        time.sleep(HEALTH_CHECK_INTERVAL)
    return False


def _parse_status_file() -> dict:
    """Parse the watcher status file written as KEY=VALUE lines."""
    status = {}
    try:
        for line in UPDATE_STATUS.read_text(encoding='utf-8').splitlines():
            if '=' not in line:
                continue
            key, value = line.split('=', 1)
            status[key.strip().lower()] = value.strip()
    except FileNotFoundError:
        return {}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Unable to read platform update watcher status: %s", exc)
    return status


def _write_update_request(update_record, mode: str = 'update') -> None:
    """Ask the host-level watcher to perform the update."""
    UPDATE_WATCH_DIR.mkdir(parents=True, exist_ok=True)
    try:
        UPDATE_STATUS.unlink(missing_ok=True)
    except TypeError:  # Python <3.8 compatibility, harmless in modern images.
        if UPDATE_STATUS.exists():
            UPDATE_STATUS.unlink()
    UPDATE_FLAG.write_text(f"{mode}:{update_record.id}\n", encoding='utf-8')


def _wait_for_watcher(update_record) -> bool:
    """Wait until the host watcher reports success/failure for this update."""
    deadline = time.monotonic() + UPDATE_WATCHER_TIMEOUT
    saw_running = False

    while time.monotonic() < deadline:
        status = _parse_status_file()
        request_id = status.get('request_id')
        state = status.get('state')

        if request_id and request_id != str(update_record.id):
            time.sleep(5)
            continue

        if state in {'running', 'success', 'failed'}:
            message = status.get('message') or f"Host watcher reported {state}."
            if not update_record.logs.rstrip().endswith(message):
                update_record.append_log(message)

        if state == 'running':
            saw_running = True
            update_record.status = 'RESTARTING'
            update_record.current_step = status.get('message') or 'Host watcher is running install.sh'
            update_record.progress_percent = min(max(update_record.progress_percent, 60), 85)
            update_record.save(update_fields=['status', 'current_step', 'progress_percent'])
        elif state == 'success':
            return True
        elif state == 'failed':
            update_record.error_message = status.get('message') or 'Host watcher reported update failure.'
            update_record.save(update_fields=['error_message'])
            return False
        elif not UPDATE_FLAG.exists() and not saw_running:
            update_record.append_log('Update flag was consumed; waiting for watcher status...')
            saw_running = True

        time.sleep(5)

    update_record.error_message = 'Timed out waiting for host update watcher status.'
    update_record.save(update_fields=['error_message'])
    return False


def perform_update(update_record) -> bool:
    """Execute a UI-triggered platform update via the host update watcher."""
    from apps.core.services.audit_service import AuditService
    from apps.deployments.models_updates import PlatformUpdate
    from django.db import transaction

    with transaction.atomic():
        active_updates = PlatformUpdate.objects.select_for_update().filter(
            status__in=['PENDING', 'PULLING', 'BACKING_UP', 'MIGRATING', 'RESTARTING', 'HEALTH_CHECK']
        ).exclude(id=update_record.id).exists()

        if active_updates:
            update_record.error_message = 'Another update is currently in progress'
            update_record.status = 'FAILED'
            update_record.save(update_fields=['error_message', 'status'])
            return False

    try:
        update_record.status = 'BACKING_UP'
        update_record.current_step = 'Recording current platform state'
        update_record.progress_percent = 10
        update_record.save(update_fields=['status', 'current_step', 'progress_percent'])
        AuditService.log("paas_update_started", target=f"update_{update_record.id}")

        snapshot = snapshot_current_state()
        update_record.snapshot_data = snapshot
        update_record.from_commit = snapshot.get('commit', '')
        update_record.save(update_fields=['snapshot_data', 'from_commit'])
        update_record.append_log(f"Current state recorded: commit={snapshot.get('commit') or 'unknown'}")

        update_record.status = 'PULLING'
        update_record.current_step = 'Queued host update watcher'
        update_record.progress_percent = 30
        update_record.save(update_fields=['status', 'current_step', 'progress_percent'])

        _write_update_request(update_record)
        update_record.append_log(
            f"Update request written to {UPDATE_FLAG}. Waiting for smsly-update-watcher to run install.sh."
        )

        if not _wait_for_watcher(update_record):
            raise PlatformUpdateError(update_record.error_message or 'Host update watcher failed.')

        update_record.status = 'HEALTH_CHECK'
        update_record.current_step = 'Verifying platform health'
        update_record.progress_percent = 90
        update_record.save(update_fields=['status', 'current_step', 'progress_percent'])

        if not check_health():
            raise PlatformUpdateError('Health check failed after update')
        update_record.append_log('Health check passed!')

        update_record.status = 'COMPLETED'
        update_record.current_step = 'Update completed successfully'
        update_record.progress_percent = 100
        update_record.completed_at = timezone.now()
        update_record.rollback_deadline = timezone.now() + timedelta(hours=1)
        update_record.save()
        update_record.append_log('✓ Update completed successfully')
        AuditService.log("paas_update_completed", target=f"update_{update_record.id}")
        return True

    except Exception as e:  # pylint: disable=broad-exception-caught
        error_msg = str(e)
        update_record.append_log(f'✗ Update failed: {error_msg}')
        update_record.error_message = error_msg
        update_record.status = 'FAILED'
        update_record.completed_at = timezone.now()
        update_record.save()
        AuditService.log("paas_update_failed", target=f"update_{update_record.id}", status="failed", message=error_msg)
        return False


def _rollback(update_record) -> bool:
    """Mark rollback as unsupported for watcher-based UI updates."""
    from apps.core.services.audit_service import AuditService

    update_record.status = 'FAILED'
    update_record.can_rollback = False
    update_record.error_message = 'Automatic rollback is not available for host watcher updates.'
    update_record.completed_at = timezone.now()
    update_record.save()
    update_record.append_log('Automatic rollback is unavailable; run the installer manually if recovery is needed.')
    AuditService.log("paas_rollback_failed", target=f"update_{update_record.id}", status="failed")
    return False
