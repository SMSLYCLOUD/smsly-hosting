"""Scrub leaked AI / template / wildcard values out of every EnvironmentVariable.

Run with::

    python manage.py scrub_env_leaks              # dry-run, report only
    python manage.py scrub_env_leaks --apply      # actually overwrite rows
    python manage.py scrub_env_leaks --apply --quiet
    python manage.py scrub_env_leaks --service <id>   # one service only

What it scrubs:
  * Backticks, smart quotes, ``"..."`` wrappers around values
  * ``{{...}}`` and ``<...>`` template wrappers
  * Trailing ``//`` / ``/* ... */`` JS comments that follow a closing quote
  * Newlines that would break a ``.env`` file (collapsed to spaces)
  * ``*`` wildcard in ALLOWED_HOSTS / CORS_ALLOWED_ORIGINS (replaced with
    the safe same-origin-only default)
  * Literal placeholders: ``{GENERATE}``, ``GENERATE``, ``{FILL_ME}``,
    ``FILL_ME``, ``<CHANGE_ME>``, ``<list-of-trusted-gateway-IP/CIDR>``,
    ``CHANGEME``, ``TODO`` (dropped or replaced with the per-key default)

What it does NOT touch:
  * Real secret values (Fernet ciphertext, hex tokens, real API keys)
  * Existing user-supplied values that are already clean
  * Locked env vars
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.deployments.models import EnvironmentVariable, Service
from apps.deployments.utils.env_sanitizer import (
    sanitize_env_value,
    is_placeholder,
    looks_wildcard_host,
)


class Command(BaseCommand):
    help = "Scrub AI / template / wildcard leaks from every EnvironmentVariable."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually overwrite rows. Without this flag the command "
            "is a dry-run and only reports what would change.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Only print the summary (still shows the per-row diff if any).",
        )
        parser.add_argument(
            "--service",
            default=None,
            help="Restrict to a single service UUID.",
        )

    def handle(self, *args, **opts):
        apply: bool = opts["apply"]
        quiet: bool = opts["quiet"]
        service_id: str | None = opts.get("service")

        qs = EnvironmentVariable.objects.all()
        if service_id:
            qs = qs.filter(service_id=service_id)
        qs = qs.select_related("service")

        total = qs.count()
        changed = 0
        dropped = 0
        wildcard_fixed = 0
        samples: list[tuple[str, str, str, str]] = []  # (svc, key, old, new)

        # Iterate in chunks to keep memory bounded
        chunk_size = 500
        offset = 0
        while offset < total:
            rows = list(qs.order_by("pk")[offset:offset + chunk_size])
            if not rows:
                break

            updates: list[EnvironmentVariable] = []
            for ev in rows:
                original = ev.value or ""
                cleaned = sanitize_env_value(original, key=ev.key, allow_empty=True)
                if cleaned is None:
                    cleaned = ""

                # Same row? Skip.
                if cleaned == original:
                    continue

                # Determine what category the change is
                is_wildcard = looks_wildcard_host(original) and ev.key.upper() in (
                    "ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS",
                    "CORS_ALLOWED_ORIGINS", "CORS_ORIGINS", "CORS_DEV_ORIGINS",
                    "ALLOWED_ORIGINS",
                )
                is_placeholder_value = is_placeholder(original) or is_placeholder(cleaned)

                if is_wildcard:
                    wildcard_fixed += 1
                if is_placeholder_value and not cleaned:
                    dropped += 1

                if not quiet and len(samples) < 40:
                    samples.append(
                        (ev.service.name, ev.key, original, cleaned)
                    )

                ev.value = cleaned
                changed += 1
                updates.append(ev)

            if apply and updates:
                with transaction.atomic():
                    for ev in updates:
                        ev.save(update_fields=["value", "updated_at"])

            offset += chunk_size

        mode = "APPLIED" if apply else "DRY-RUN"
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"[{mode}] scrubbed {changed} / {total} env vars"
        ))
        self.stdout.write(
            f"  wildcards normalized: {wildcard_fixed}"
        )
        self.stdout.write(
            f"  placeholders dropped/blanked: {dropped}"
        )
        if not apply and changed:
            self.stdout.write(self.style.WARNING(
                "  Re-run with --apply to persist these changes."
            ))
        if not quiet and samples:
            self.stdout.write("")
            self.stdout.write("Sample changes (up to 40):")
            for svc, key, old, new in samples:
                old_disp = (old[:80] + "...") if len(old) > 80 else old
                new_disp = (new[:80] + "...") if len(new) > 80 else new
                self.stdout.write(f"  {svc} :: {key}")
                self.stdout.write(f"    - {old_disp!r}")
                self.stdout.write(f"    + {new_disp!r}")
