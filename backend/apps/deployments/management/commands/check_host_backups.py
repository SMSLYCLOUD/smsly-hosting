"""Check the host-level backup.sh output and report status.

Usage:
    python manage.py check_host_backups [--alert-on-failure] [--json]

Reads the backup log file and most recent backup, checks age/size/exit code.
If ``--alert-on-failure`` is passed and the backup is stale/missing/corrupt,
dispatches a notification via ``dispatch_notification.delay()`` to all
superusers.
"""
import json
import os
import sys
from datetime import UTC, datetime

from django.core.management.base import BaseCommand

BACKUP_DIR_DEFAULT = "/opt/smsly-hosting/backups"
STALE_THRESHOLD_HOURS = 26


class Command(BaseCommand):
    help = "Check host-level backup.sh status and optionally alert on failure."

    def add_arguments(self, parser):
        parser.add_argument(
            "--alert-on-failure",
            action="store_true",
            help="Dispatch notifications to superusers if backup is stale/missing.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output status as JSON (for scripting).",
        )

    def handle(self, *args, **options):
        backup_dir = os.environ.get("BACKUP_DIR", BACKUP_DIR_DEFAULT)
        alert_on_failure = options["alert_on_failure"]
        output_json = options["json"]

        status = self._check_backups(backup_dir)

        if output_json:
            self.stdout.write(json.dumps(status))
        else:
            self._print_status(status)

        if alert_on_failure and status["status"] != "OK":
            self._send_alerts(status)

        if status["status"] != "OK":
            sys.exit(1)

    def _check_backups(self, backup_dir: str) -> dict:
        status = {
            "status": "OK",
            "latest_file": None,
            "age_hours": None,
            "size_bytes": None,
            "log_tail": None,
            "checked_at": datetime.now(UTC).isoformat(),
        }

        if not os.path.isdir(backup_dir):
            status["status"] = "MISSING_DIR"
            status["error"] = f"Backup directory not found: {backup_dir}"
            return status

        import glob as _glob
        pattern = os.path.join(backup_dir, "smsly_hosting_*.sql.gz*")
        files = sorted(_glob.glob(pattern), reverse=True)
        if not files:
            # Also try .enc extension
            pattern = os.path.join(backup_dir, "smsly_hosting_*.enc")
            files = sorted(_glob.glob(pattern), reverse=True)

        if not files:
            status["status"] = "MISSING"
            status["error"] = "No backup files found."
            return status

        latest = files[0]
        status["latest_file"] = os.path.basename(latest)
        try:
            stat_info = os.stat(latest)
            status["size_bytes"] = stat_info.st_size
            age_seconds = datetime.now().timestamp() - stat_info.st_mtime
            status["age_hours"] = round(age_seconds / 3600, 2)
        except OSError as exc:
            status["status"] = "UNREADABLE"
            status["error"] = str(exc)
            return status

        if status["age_hours"] is not None and status["age_hours"] > STALE_THRESHOLD_HOURS:
            status["status"] = "STALE"

        if status.get("size_bytes", 0) == 0:
            status["status"] = "EMPTY"

        # Read last line of backup log
        log_path = os.path.join(backup_dir, "backup.log")
        if os.path.isfile(log_path):
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    if lines:
                        status["log_tail"] = lines[-1].strip()
                        if "error" in lines[-1].lower() or "failed" in lines[-1].lower():
                            status["status"] = "LOG_ERROR"
            except OSError:
                pass

        return status

    def _print_status(self, status: dict) -> None:
        status_icons = {
            "OK": "\033[0;32m✓\033[0m",
            "STALE": "\033[1;33m⚠\033[0m",
            "MISSING": "\033[0;31m✗\033[0m",
            "MISSING_DIR": "\033[0;31m✗\033[0m",
            "EMPTY": "\033[0;31m✗\033[0m",
            "UNREADABLE": "\033[0;31m✗\033[0m",
            "LOG_ERROR": "\033[1;33m⚠\033[0m",
        }
        icon = status_icons.get(status["status"], "?")
        self.stdout.write(f"Host Backup Status: {icon} {status['status']}")
        if status.get("latest_file"):
            self.stdout.write(f"  Latest file: {status['latest_file']}")
        if status.get("age_hours") is not None:
            self.stdout.write(f"  Age: {status['age_hours']} hours")
        if status.get("size_bytes"):
            size_mb = status["size_bytes"] / (1024 * 1024)
            self.stdout.write(f"  Size: {size_mb:.1f} MB")
        if status.get("log_tail"):
            self.stdout.write(f"  Log tail: {status['log_tail']}")
        if status.get("error"):
            self.stdout.write(f"  Error: {status['error']}")

    def _send_alerts(self, status: dict) -> None:
        try:
            from django.contrib.auth import get_user_model

            from apps.notifications.tasks import dispatch_notification

            for user in get_user_model().objects.filter(is_superuser=True, is_active=True):
                dispatch_notification.delay(
                    event_type="host_backup_issue",
                    user_id=user.id,
                    title=f"Host backup {status['status'].lower()}",
                    message=(
                        f"Host-level backup ({status.get('latest_file', 'unknown')}) "
                        f"is {status['status'].lower()}. "
                        f"Age: {status.get('age_hours', '?')}h. "
                        f"Check scripts/backup.sh on the controller."
                    ),
                    metadata={"backup_status": status},
                )
            self.stdout.write("Alerts dispatched to superusers.")
        except Exception as exc:
            self.stderr.write(f"Failed to send alerts: {exc}")
