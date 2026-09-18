"""Manual controls for the shared Postgres addon instance.

The hourly watchdog (check_shared_postgres_ha_task) owns the automatic
path; this command is the manual counterpart for drills, stalked states,
and recovery the watchdog correctly refuses (stale standby, cooldown).

Usage (on the host, any backend/worker container has the code; docker
calls go through the socket-proxy — run from the host with the repo's
venv, or docker exec into backend):
  python manage.py shared_postgres_ha status
  python manage.py shared_postgres_ha promote [--force]
  python manage.py shared_postgres_ha reseed
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Inspect, fail over, or reseed the shared Postgres addon instance."

    def add_arguments(self, parser):
        parser.add_argument(
            "action", choices=["status", "promote", "reseed"],
            help="status: print HA state; promote: fail over to the "
                 "standby (refuses a live primary without --force); "
                 "reseed: rebuild the standby from the primary.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="promote even while the primary answers (DR drills only).",
        )

    def handle(self, *args, **options):
        from apps.addons.services import shared_postgres as sp

        action = options["action"]
        if action == "status":
            status = sp.shared_ha_status()
            self.stdout.write(
                f"state={status['state']} primary={status['primary']} "
                f"standby={status['standby']} lag_seconds={status['lag_seconds']}"
            )
            return
        try:
            if action == "promote":
                new_primary = sp.promote_shared_standby(
                    force=bool(options["force"]))
                self.stdout.write(self.style.SUCCESS(
                    f"promoted; primary is now {new_primary}"))
            elif action == "reseed":
                name = sp.ensure_shared_standby(reseed=True)
                self.stdout.write(self.style.SUCCESS(
                    f"standby reseeded and streaming ({name})"))
        except RuntimeError as exc:
            raise CommandError(str(exc))
