"""
Management command to re-encrypt all encrypted model fields.

Use cases:
- FIELD_ENCRYPTION_KEY was rotated and stale ciphertexts need re-encryption
- Agent-lite node has a different key than master and values were encrypted with the wrong key
- Recovery after key mismatch causes DECRYPTION_FAILURE spam

Usage:
    # Dry-run: scan and report how many fields are valid/decryptable vs stale
    python manage.py reencrypt_fields --dry-run

    # Re-encrypt all encrypted fields using the current FIELD_ENCRYPTION_KEY
    python manage.py reencrypt_fields

    # Re-encrypt with a specific OLD key (decrypt with old, encrypt with current)
    python manage.py reencrypt_fields --old-key <base64-fernett-key>

    # Re-encrypt a specific model
    python manage.py reencrypt_fields --model deployments.EnvironmentVariable

    # Re-encrypt a specific model+field
    python manage.py reencrypt_fields --model deployments.EnvironmentVariable --field value
"""
from django.core.management.base import BaseCommand, CommandError
from django.apps import apps
from django.conf import settings
from django.db import connection
from encrypted_model_fields.fields import EncryptedCharField, EncryptedTextField


def _get_encrypted_fields(model):
    """Yield (field_name, field_instance) for all encrypted fields on a model."""
    for f in model._meta.get_fields():
        if isinstance(f, (EncryptedCharField, EncryptedTextField)):
            yield f.name, f


def _is_fernet_token(value):
    return isinstance(value, str) and value.startswith("gAAAAA")


class Command(BaseCommand):
    help = "Re-encrypt encrypted model fields (e.g., after FIELD_ENCRYPTION_KEY rotation)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Scan and report status without making changes",
        )
        parser.add_argument(
            "--old-key",
            default=None,
            help="Previous FIELD_ENCRYPTION_KEY (base64 Fernet) to decrypt with; "
                 "if omitted, the current key is used for both decrypt and encrypt",
        )
        parser.add_argument(
            "--model",
            default=None,
            help="Filter to a specific model (e.g., 'deployments.EnvironmentVariable')",
        )
        parser.add_argument(
            "--field",
            default=None,
            help="Filter to a specific field name on the model",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Batch size for the update loop (default: 500)",
        )

    def handle(self, **options):
        dry_run = options["dry_run"]
        old_key = options["old_key"]
        model_filter = options["model"]
        field_filter = options["field"]
        batch_size = options["batch_size"]

        from cryptography.fernet import Fernet

        current_key = str(getattr(settings, "FIELD_ENCRYPTION_KEY", ""))
        if not current_key:
            raise CommandError("FIELD_ENCRYPTION_KEY is not set in settings")

        fernet_current = Fernet(current_key.encode() if isinstance(current_key, str) else current_key)

        if old_key:
            fernet_old = Fernet(old_key.encode() if isinstance(old_key, str) else old_key)
        else:
            fernet_old = fernet_current

        target_models = self._collect_models(model_filter)

        total_ok = 0
        total_fixed = 0
        total_failed = 0
        total_empty = 0

        for model in target_models:
            for field_name, field_instance in _get_encrypted_fields(model):
                if field_filter and field_name != field_filter:
                    continue

                table = model._meta.db_table
                column = field_instance.column
                pk_col = model._meta.pk.column

                self.stdout.write(f"\n[{model._meta.label}]{field_name} (scanning...)")

                ok = 0
                fixed = 0
                failed = 0
                empty = 0

                offset = 0
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            f"SELECT {pk_col}, {column} FROM {table} "
                            f"ORDER BY {pk_col} LIMIT {batch_size} OFFSET {offset}"
                        )
                        rows = cursor.fetchall()
                    if not rows:
                        break

                    updates = []
                    for pk, raw_val in rows:
                        if not raw_val:
                            empty += 1
                            continue

                        if not _is_fernet_token(raw_val):
                            ok += 1
                            continue

                        try:
                            decrypted = fernet_old.decrypt(raw_val.encode())
                        except Exception:
                            failed += 1
                            continue

                        try:
                            re_encrypted = fernet_current.encrypt(decrypted).decode()
                        except Exception:
                            failed += 1
                            continue

                        # Check if re-encryption actually changed the ciphertext
                        if re_encrypted == raw_val:
                            ok += 1
                        else:
                            updates.append((re_encrypted, pk))

                    if updates and not dry_run:
                        with connection.cursor() as cursor:
                            cursor.executemany(
                                f"UPDATE {table} SET {column} = %s WHERE {pk_col} = %s",
                                updates,
                            )
                        fixed += len(updates)
                    elif updates:
                        fixed += len(updates)

                    offset += batch_size

                self.stdout.write(
                    f"  OK={ok}  fixed={fixed}  empty={empty}  failed={failed}"
                    + ("  [DRY-RUN]" if dry_run else "")
                )

                total_ok += ok
                total_fixed += fixed
                total_failed += failed
                total_empty += empty

        self.stdout.write(self.style.SUCCESS(
            f"\nDone{' (dry-run)' if dry_run else ''}: "
            f"OK={total_ok}, fixed={total_fixed}, empty={total_empty}, failed={total_failed}"
        ))

        if total_failed:
            self.stderr.write(
                self.style.WARNING(
                    f"  {total_failed} field(s) could not be decrypted — "
                    "they may have been encrypted with yet another key."
                )
            )

    def _collect_models(self, model_filter):
        if model_filter:
            try:
                app_label, model_name = model_filter.split(".")
                return [apps.get_model(app_label, model_name)]
            except (ValueError, LookupError) as e:
                raise CommandError(f"Invalid --model '{model_filter}': {e}")

        models = []
        for app_config in apps.get_app_configs():
            for model in app_config.get_models():
                if list(_get_encrypted_fields(model)):
                    models.append(model)
        return models
