"""Import BackupEncryptionKey rows from a JSON export file.

Usage:
    python manage.py import_backup_keys <keys.json> [--dry-run]

Idempotent: skips keys where key_id already exists with matching fingerprint.
Raises an error on fingerprint mismatch (operator must resolve conflict).
"""
import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Import BackupEncryptionKey rows from a JSON export file."

    def add_arguments(self, parser):
        parser.add_argument(
            "input_file",
            help="Path to the JSON export file from export_backup_keys.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the file without writing to the database.",
        )

    def handle(self, *args, **options):
        from django.utils import timezone as tz

        from apps.deployments.models.backup import BackupEncryptionKey

        input_path = options["input_file"]
        dry_run = options["dry_run"]

        try:
            with open(input_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.stderr.write(f"Failed to read input file: {exc}")
            raise SystemExit(1) from exc

        version = data.get("version")
        if version != 1:
            self.stderr.write(f"Unsupported export version: {version}")
            raise SystemExit(1)

        keys = data.get("keys", [])
        if not keys:
            self.stdout.write(self.style.WARNING("No keys found in export file."))
            return

        imported = 0
        skipped = 0
        conflicts = 0

        for key_data in keys:
            key_id = key_data.get("key_id")
            fingerprint = key_data.get("fingerprint")
            key_material = key_data.get("key_material")

            if not key_id or not fingerprint or not key_material:
                self.stderr.write(f"Skipping incomplete entry: {key_id or '(missing id)'}")
                skipped += 1
                continue

            existing = BackupEncryptionKey.objects.filter(key_id=key_id).first()
            if existing:
                if existing.fingerprint != fingerprint:
                    self.stderr.write(
                        self.style.ERROR(
                            f"CONFLICT: key_id={key_id} exists with fingerprint "
                            f"'{existing.fingerprint}', but import has '{fingerprint}'. "
                            f"Resolve manually before proceeding."
                        )
                    )
                    conflicts += 1
                    continue
                self.stdout.write(f"Skipping {key_id} (already exists with matching fingerprint).")
                skipped += 1
                continue

            if dry_run:
                self.stdout.write(f"[DRY RUN] Would import: {key_id} ({fingerprint})")
                imported += 1
            else:
                BackupEncryptionKey.objects.create(
                    key_id=key_id,
                    fingerprint=fingerprint,
                    key_material_encrypted=key_material,
                    source="IMPORTED",
                    label=key_data.get("label", ""),
                    is_active=False,
                    last_used_at=None,
                    created_at=tz.now(),
                )
                self.stdout.write(f"Imported: {key_id}")
                imported += 1

        summary = f"Imported: {imported}, Skipped: {skipped}"
        if conflicts:
            summary += f", Conflicts: {conflicts}"
            self.stdout.write(self.style.ERROR(summary))
            raise SystemExit(1)
        else:
            self.stdout.write(self.style.SUCCESS(summary))
