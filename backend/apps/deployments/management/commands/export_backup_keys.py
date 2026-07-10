"""Export all BackupEncryptionKey rows to a JSON file for offline escrow.

Usage:
    python manage.py export_backup_keys [--output keys.json] [--pretty]

Output JSON structure:
{
  "exported_at": "2026-...",
  "version": 1,
  "keys": [
    {
      "key_id": "a1b2c3d4",
      "fingerprint": "e5f6a7b8",
      "source": "AUTO",
      "label": "master-a-imported-...",
      "created_at": "2026-...",
      "is_active": true,
      "key_material": "gAAAAAB..."  // still FIELD_ENCRYPTION_KEY encrypted
    }
  ],
  "instructions": "Store this file offline. Keys remain FIELD_ENCRYPTION_KEY encrypted."
}

The keys remain encrypted with FIELD_ENCRYPTION_KEY — the operator needs
the same FIELD_ENCRYPTION_KEY on the target master to decrypt them.
"""
import json
import os
from datetime import UTC, datetime

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Export all BackupEncryptionKey rows to a JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="backup-keys-export.json",
            help="Output file path (default: backup-keys-export.json)",
        )
        parser.add_argument(
            "--pretty",
            action="store_true",
            help="Pretty-print the JSON output.",
        )

    def handle(self, *args, **options):
        from apps.deployments.models_backup import BackupEncryptionKey

        keys_qs = BackupEncryptionKey.objects.all()
        count = keys_qs.count()

        if count == 0:
            self.stdout.write(self.style.WARNING("No BackupEncryptionKey rows found."))
            return

        keys_data = []
        for key in keys_qs:
            keys_data.append({
                "key_id": key.key_id,
                "fingerprint": key.fingerprint,
                "source": key.source,
                "label": key.label,
                "created_at": key.created_at.isoformat() if key.created_at else None,
                "is_active": key.is_active,
                "key_material": key.key_material_encrypted,
                "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
            })

        output = {
            "exported_at": datetime.now(UTC).isoformat(),
            "version": 1,
            "keys": keys_data,
            "instructions": (
                "Store this file offline. The key_material values are encrypted "
                "with FIELD_ENCRYPTION_KEY and can only be decrypted on a master "
                "that has the same FIELD_ENCRYPTION_KEY set. "
                "To restore these keys, use 'manage.py import_backup_keys'."
            ),
        }

        indent = 2 if options["pretty"] else None
        json_str = json.dumps(output, indent=indent, ensure_ascii=False)

        output_path = options["output"]
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(json_str)

        os.chmod(output_path, 0o600)

        self.stdout.write(
            self.style.SUCCESS(
                f"Exported {count} BackupEncryptionKey row(s) to {output_path}"
            )
        )
