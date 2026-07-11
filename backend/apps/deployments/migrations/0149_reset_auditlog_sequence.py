"""
Reset the deployments_auditlog_id_seq sequence to max(id)+1.

The sequence drifted behind the actual table max after rows were inserted
with explicit IDs (e.g. data migrations, pg_restore with --disable-triggers).
PostgreSQL's BIGSERIAL/BigAutoField sequence is not automatically updated when
rows are inserted bypassing the sequence, causing duplicate-key errors on the
next INSERT.

setval(seq, max_id, true) sets the sequence so the *next* value returned by
nextval() is max_id + 1, matching PostgreSQL docs for the third argument:
  is_called=true -> next call to nextval() returns last_value + increment
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0148_widen_cloud_storage_key_fields"),
    ]

    operations = [
        migrations.RunSQL(
            # Forward: advance sequence past current max row ID.
            # COALESCE handles an empty table (sets sequence to 1).
            sql="""
                SELECT setval(
                    pg_get_serial_sequence('deployments_auditlog', 'id'),
                    COALESCE(MAX(id), 1),
                    true
                )
                FROM deployments_auditlog;
            """,
            # Reverse: no meaningful rollback for a sequence reset.
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
