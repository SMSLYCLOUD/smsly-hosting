from django.db import migrations


def use_thirty_minute_default(apps, schema_editor):
    PlatformConfig = apps.get_model('deployments', 'PlatformConfig')
    # Upgrade only values written by the previous defaults. Preserve an
    # operator's explicit custom interval.
    PlatformConfig.objects.filter(
        ecosystem_wave_recheck_seconds__in=(15, 300),
    ).update(ecosystem_wave_recheck_seconds=1800)


class Migration(migrations.Migration):
    dependencies = [
        ('deployments', '0200_ecosystem_wave_recheck_default'),
    ]

    operations = [
        migrations.RunPython(use_thirty_minute_default, migrations.RunPython.noop),
    ]
