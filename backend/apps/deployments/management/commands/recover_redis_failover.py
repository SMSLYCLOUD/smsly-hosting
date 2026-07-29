"""
recover_redis_failover — Detect and fix orphaned Redis primary after Sentinel failover.

Usage:
    docker compose exec backend python manage.py recover_redis_failover
    docker compose exec backend python manage.py recover_redis_failover --dry-run
"""

import logging

from django.core.management.base import BaseCommand

from ...services.redis_failover_recovery import check_and_recover

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Detect and clean up orphaned Redis primary after Sentinel failover."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Log actions without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        result = check_and_recover(dry_run=dry_run)

        status_colors = {
            "ok": self.style.SUCCESS,
            "recovered": self.style.SUCCESS,
            "dry_run": self.style.WARNING,
            "skipped": self.style.WARNING,
            "not_found": self.style.WARNING,
            "error": self.style.ERROR,
        }
        color = status_colors.get(result["status"], self.style.WARNING)
        self.stdout.write(color(f"[{result['status']}] {result['message']}"))
