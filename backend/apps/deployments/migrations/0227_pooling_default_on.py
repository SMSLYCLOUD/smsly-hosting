# Tenant pooler (pgcat-tenants) enabled by default.

from django.db import migrations, models


def _enable_pooling_default(apps, schema_editor):
    # Sticky semantics make this safe: only NEW shared provisions route
    # through the pooler; existing addons keep dialling the server
    # directly regardless of this flag.
    PlatformConfig = apps.get_model('deployments', 'PlatformConfig')
    PlatformConfig.objects.filter(tenant_pooling_enabled=False).update(
        tenant_pooling_enabled=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0226_platformconfig_openappsec_mode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='platformconfig',
            name='tenant_pooling_enabled',
            field=models.BooleanField(default=True, help_text='Route NEW shared Postgres addons through the pgcat-tenants pooler instead of direct connections. Existing shared addons keep dialling the server directly (sticky).'),
        ),
        migrations.RunPython(
            _enable_pooling_default, migrations.RunPython.noop,
        ),
    ]
