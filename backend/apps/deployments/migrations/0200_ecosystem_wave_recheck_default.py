from django.db import migrations, models


def raise_legacy_default(apps, schema_editor):
    PlatformConfig = apps.get_model('deployments', 'PlatformConfig')
    PlatformConfig.objects.filter(ecosystem_wave_recheck_seconds=15).update(
        ecosystem_wave_recheck_seconds=1800,
    )


class Migration(migrations.Migration):
    dependencies = [
        ('deployments', '0199_service_external_ha_connection'),
    ]

    operations = [
        migrations.AlterField(
            model_name='platformconfig',
            name='ecosystem_wave_recheck_seconds',
            field=models.PositiveIntegerField(
                default=1800,
                help_text='Seconds to wait before checking whether the previous ecosystem wave finished',
            ),
        ),
        migrations.RunPython(raise_legacy_default, migrations.RunPython.noop),
    ]
