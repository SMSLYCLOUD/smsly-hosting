from django.db import migrations, models


def enable_promos_on_existing_row(apps, schema_editor):
    PlatformConfig = apps.get_model('deployments', 'PlatformConfig')
    # Operator decision: promos are on by default. Existing singleton
    # rows (created before this default existed) get enabled; admins
    # can turn it back off in Settings → Platform → Trulay Promos.
    PlatformConfig.objects.filter(pk=1).update(promos_enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0248_platformconfig_promos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='platformconfig',
            name='promos_enabled',
            field=models.BooleanField(default=True, help_text='Master kill-switch for the dashboard Trulay cross-sell banner. On by default; turn off for ad-free self-hosts.'),
        ),
        migrations.RunPython(enable_promos_on_existing_row, migrations.RunPython.noop),
    ]
