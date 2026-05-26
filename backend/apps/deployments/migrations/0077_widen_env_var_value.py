import encrypted_model_fields.fields
from django.db import migrations

def check_corrupt_env_vars(apps, schema_editor):
    EnvironmentVariable = apps.get_model('deployments', 'EnvironmentVariable')
    import logging
    logger = logging.getLogger(__name__)
    
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id, service_id, key, value FROM {EnvironmentVariable._meta.db_table}"
        )
        for row in cursor.fetchall():
            var_id, service_id, key, raw_val = row
            if raw_val and len(raw_val) == 255 and raw_val.startswith('gAAAAA'):
                logger.warning(
                    "[DB-MIGRATION] Environment variable key='%s' (id=%s) for service_id=%s "
                    "appears to be truncated at 255 characters. This value is likely corrupted.",
                    key, var_id, service_id
                )

class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0076_addon_deletion_error'),
    ]

    operations = [
        migrations.AlterField(
            model_name='environmentvariable',
            name='value',
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True, default='', max_length=10000),
        ),
        migrations.RunPython(check_corrupt_env_vars, reverse_code=migrations.RunPython.noop),
    ]
