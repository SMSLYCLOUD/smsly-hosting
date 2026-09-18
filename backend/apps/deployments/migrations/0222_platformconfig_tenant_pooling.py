# 2026-09-18: tenant_pooling_enabled gates per-tenant pgcat pooling for
# NEW shared Postgres addons (default off; existing shared addons stay on
# direct connections — sticky, never broken by the toggle).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0221_addon_provision_mode_defer'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='tenant_pooling_enabled',
            field=models.BooleanField(default=False, help_text='Route NEW shared Postgres addons through the pgcat-tenants pooler instead of direct connections. Existing shared addons keep dialling the server directly (sticky).'),
        ),
    ]
