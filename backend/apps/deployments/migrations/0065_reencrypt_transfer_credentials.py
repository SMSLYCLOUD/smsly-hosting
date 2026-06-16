"""
Re-encrypt any ServerTransfer credentials that may still be stored as
plaintext from before migration 0054 switched to EncryptedTextField.

Bypasses from_db_value (would fail on plaintext) by setting fields via
__dict__ directly, then save() triggers get_prep_value which encrypts.
"""
from django.db import migrations


BATCH_SIZE = 200


def re_encrypt_transfer_credentials(apps, schema_editor):
    ServerTransfer = apps.get_model("deployments", "ServerTransfer")
    connection = schema_editor.connection
    table = ServerTransfer._meta.db_table
    count = 0
    skipped = 0
    pending = []

    def is_encrypted(val):
        return isinstance(val, str) and val.startswith("gAAAAA")

    # Raw SELECT bypasses the EncryptedTextField `from_db_value` descriptor
    # (which would try to Fernet-decrypt plaintext rows and fail).
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT id, target_ssh_key, target_ssh_password FROM {table}")
        rows = cursor.fetchall()

    def flush():
        nonlocal count, pending
        if not pending:
            return
        # bulk_update runs get_prep_value on each field, which is where the
        # Fernet encryption happens — same code path as save() but in one
        # statement per BATCH_SIZE rows instead of one per row.
        ServerTransfer.objects.bulk_update(
            pending,
            fields=["target_ssh_key", "target_ssh_password"],
        )
        count += len(pending)
        pending = []

    for pk, raw_key, raw_password in rows:
        if not raw_key and not raw_password:
            continue

        if is_encrypted(raw_key) and is_encrypted(raw_password):
            skipped += 1
            continue

        # Build an unsaved instance bypassing from_db_value, then queue
        # for batched update.
        transfer = ServerTransfer(pk=pk)
        transfer._state.adding = False
        if raw_key and not is_encrypted(raw_key):
            transfer.__dict__["target_ssh_key"] = raw_key
        if raw_password and not is_encrypted(raw_password):
            transfer.__dict__["target_ssh_password"] = raw_password
        pending.append(transfer)

        if len(pending) >= BATCH_SIZE:
            flush()

    flush()

    total = count + skipped
    if total:
        print(
            f"Re-encrypted {count} ServerTransfer credential(s); "
            f"{skipped} already-encrypted/skipped record(s)."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("deployments", "0064_deployment_target_is_local"),
    ]

    operations = [
        migrations.RunPython(
            re_encrypt_transfer_credentials,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

