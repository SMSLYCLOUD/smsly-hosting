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
SMSLY_BRANCH = os.environ.get('SMSLY_BRANCH', 'main')
# Read COMPOSE_FILE from the env var set by install.sh, falling back to
# the base docker-compose.yml (which is what the master stack uses) and
# then the prod overlay. This matches the file actually started by
# `docker compose up -d` on the host.
COMPOSE_FILE = os.environ.get(
    'COMPOSE_FILE',
    os.path.join(INSTALL_DIR, 'docker-compose.yml'),
)
# Shared bind mount used by caddy-watcher and smsly-update-watcher.  Inside the
# containers it is mounted at /caddy-config; on the host it is
# /opt/smsly-hosting/caddy-config.
UPDATE_WATCH_DIR = Path(os.environ.get('PLATFORM_UPDATE_WATCH_DIR', '/caddy-config'))
UPDATE_FLAG = UPDATE_WATCH_DIR / '.update'
UPDATE_STATUS = UPDATE_WATCH_DIR / '.update.status'
HEALTH_CHECK_URL = os.environ.get('PLATFORM_HEALTH_CHECK_URL', 'http://127.0.0.1:8000/health')
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
    last_log_pos = 0
    wait_cycles = 0
    install_log_path = UPDATE_WATCH_DIR / "install.log"

    def _sync_install_logs():
        nonlocal last_log_pos
        if install_log_path.exists():
            try:
                with open(install_log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_log_pos)
                    chunk = f.read()
                    if chunk:
                        last_log_pos = f.tell()
                        update_record.append_log(chunk)
            except Exception as exc:
                logger.debug("Error reading install.log: %s", exc)

    while time.monotonic() < deadline:
        _sync_install_logs()
        status = _parse_status_file()
        request_id = status.get('request_id')
        state = status.get('state')

        if request_id and request_id != str(update_record.id):
            time.sleep(3)
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
            _sync_install_logs()
            return True
        elif state == 'failed':
            _sync_install_logs()
            update_record.error_message = status.get('message') or 'Host watcher reported update failure.'
            update_record.save(update_fields=['error_message'])
            return False
        elif not UPDATE_FLAG.exists() and not saw_running:
            update_record.append_log('Update flag was consumed; waiting for watcher status...')
            saw_running = True
        elif UPDATE_FLAG.exists() and not saw_running:
            wait_cycles += 1
            if wait_cycles == 5:
                update_record.append_log('⏳ Waiting for host service (smsly-update-watcher) to detect update request...')
            elif wait_cycles == 15:
                update_record.append_log('⚠️ Host update watcher has not responded after 45 seconds. Verify that smsly-update-watcher.service is running on the host OS (sudo systemctl enable --now smsly-update-watcher).')
            elif wait_cycles == 30:
                update_record.append_log('⚠️ Still waiting for host watcher. If running inside a container without host systemd integration, run self-update manually on the host: sudo bash install.sh --update')

        time.sleep(3)

    _sync_install_logs()
    update_record.error_message = 'Timed out waiting for host update watcher status.'
    update_record.save(update_fields=['error_message'])
    return False


def perform_update(update_record) -> bool:
    """Execute a UI-triggered platform update via the host update watcher."""
    from apps.core.services.audit_service import AuditService
    from apps.deployments.models_updates import PlatformUpdate
    from django.db import transaction

    if update_record.status != 'PENDING':
        logger.warning(
            "Refusing perform_update on record %s (status=%s): not PENDING. Preventing restart loop.",
            update_record.id, update_record.status,
        )
        return False

    with transaction.atomic():
        active_updates = PlatformUpdate.objects.select_for_update().filter(
            status__in=['PENDING', 'PULLING', 'BACKING_UP', 'MIGRATING', 'RESTARTING', 'HEALTH_CHECK']
        ).exclude(id=update_record.id)

        if active_updates.exists():
            # Clear stale in-progress updates instead of failing the new one
            cleared = 0
            for prev in active_updates:
                prev.status = 'FAILED'
                prev.error_message = 'Cleared stale update to allow new update to proceed.'
                prev.completed_at = timezone.now()
                prev.append_log('✗ Cleared as stale to allow new update to proceed.')
                prev.save()
                cleared += 1
                logger.info("Cleared stale platform update %s (was %s) to allow %s",
                            prev.id, prev.status, update_record.id)

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
    """Roll back to the previous git commit captured in snapshot_data."""
    from apps.core.services.audit_service import AuditService

    if update_record.status in {'ROLLED_BACK', 'FAILED'}:
        logger.warning(
            "Refusing _rollback on record %s (status=%s): already in terminal state.",
            update_record.id, update_record.status,
        )
        return False

    prev_commit = (update_record.snapshot_data or {}).get('commit', '')
    if not prev_commit:
        update_record.status = 'FAILED'
        update_record.can_rollback = False
        update_record.error_message = 'No previous commit recorded — cannot rollback.'
        update_record.completed_at = timezone.now()
        update_record.save()
        update_record.append_log('Rollback failed: no previous commit in snapshot.')
        AuditService.log("paas_rollback_failed", target=f"update_{update_record.id}", status="failed")
        return False

    update_record.append_log(f'Rolling back to commit {prev_commit[:8]}...')
    try:
        # Checkout the previous commit and re-run the installer.
        ok, out = _run(['git', 'fetch', 'origin', SMSLY_BRANCH])
        update_record.append_log(f'git fetch: {out[:500]}')
        ok, out = _run(['git', 'checkout', prev_commit])
        update_record.append_log(f'git checkout: {out[:500]}')
        if not ok:
            raise RuntimeError(f'git checkout failed: {out}')

        # Trigger a fast redeploy of core services with the old code.
        ok, out = _run(
            ['bash', str(INSTALL_DIR / 'install.sh'), '--update-backend', '--no-screen'],
            env={**os.environ, 'NON_INTERACTIVE': '1', 'NO_SCREEN': 'true'},
        )
        update_record.append_log(f'install.sh --update-backend: {out[:500]}')
        update_record.status = 'ROLLED_BACK'
        update_record.can_rollback = False
        update_record.completed_at = timezone.now()
        update_record.save()
        AuditService.log("paas_rollback_success", target=f"update_{update_record.id}", status="rolled_back")
        return True
    except Exception as exc:
        update_record.status = 'FAILED'
        update_record.can_rollback = False
        update_record.error_message = str(exc)[:2000]
        update_record.completed_at = timezone.now()
        update_record.save()
        update_record.append_log(f'Rollback failed: {exc}')
        AuditService.log("paas_rollback_failed", target=f"update_{update_record.id}", status="failed")
        return False
