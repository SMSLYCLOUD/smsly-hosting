from django.db import migrations, models


def repair_postgres_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE deployments_service "
            "ALTER COLUMN wildcard_url_enabled SET DEFAULT true, "
            "ALTER COLUMN wildcard_url_enabled DROP NOT NULL, "
            "ALTER COLUMN node_url_enabled SET DEFAULT true, "
            "ALTER COLUMN node_url_enabled DROP NOT NULL"
        )


def reverse_repair_postgres_columns(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE deployments_service "
            "ALTER COLUMN wildcard_url_enabled SET NOT NULL, "
            "ALTER COLUMN wildcard_url_enabled DROP DEFAULT, "
            "ALTER COLUMN node_url_enabled SET NOT NULL, "
            "ALTER COLUMN node_url_enabled DROP DEFAULT"
        )


class Migration(migrations.Migration):
    """Fix: columns were NOT NULL with no default despite migrations saying
    null=True. Manually ALTER'd on production; this migration keeps Django
    in sync for fresh installs."""

    dependencies = [
        ('deployments', '0174_managedserver_node_domain'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    repair_postgres_columns,
                    reverse_repair_postgres_columns,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="service",
                    name="wildcard_url_enabled",
                    field=models.BooleanField(default=True, null=True, blank=True),
                ),
                migrations.AlterField(
                    model_name="service",
                    name="node_url_enabled",
                    field=models.BooleanField(default=True, null=True, blank=True),
                ),
            ],
        ),
    ]
