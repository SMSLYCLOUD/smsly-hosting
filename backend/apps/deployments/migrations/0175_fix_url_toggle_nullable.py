from django.db import migrations, models


class Migration(migrations.Migration):
    """Fix: columns were NOT NULL with no default despite migrations saying
    null=True. Manually ALTER'd on production; this migration keeps Django
    in sync for fresh installs."""

    dependencies = [
        ('deployments', '0174_managedserver_node_domain'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                "ALTER TABLE deployments_service ALTER COLUMN wildcard_url_enabled SET DEFAULT true",
                "ALTER TABLE deployments_service ALTER COLUMN wildcard_url_enabled DROP NOT NULL",
                "ALTER TABLE deployments_service ALTER COLUMN node_url_enabled SET DEFAULT true",
                "ALTER TABLE deployments_service ALTER COLUMN node_url_enabled DROP NOT NULL",
            ],
            reverse_sql=[
                "ALTER TABLE deployments_service ALTER COLUMN wildcard_url_enabled SET NOT NULL",
                "ALTER TABLE deployments_service ALTER COLUMN wildcard_url_enabled DROP DEFAULT",
                "ALTER TABLE deployments_service ALTER COLUMN node_url_enabled SET NOT NULL",
                "ALTER TABLE deployments_service ALTER COLUMN node_url_enabled DROP DEFAULT",
            ],
        ),
    ]
