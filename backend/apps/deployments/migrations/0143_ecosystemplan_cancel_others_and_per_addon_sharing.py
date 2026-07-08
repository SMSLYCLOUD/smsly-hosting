from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0142_platformconfig_traffic_geo_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='ecosystemplan',
            name='cancel_others_on_failure',
            field=models.BooleanField(
                default=False,
                help_text='When True, if any service deployment fails, all remaining queued deployments in the ecosystem are cancelled.',
            ),
        ),
        migrations.AddField(
            model_name='ecosystemplan',
            name='shared_addon_config',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Per-addon sharing configuration. Keys are addon types (e.g. "POSTGRES", "REDIS"), values are objects with "shared" (bool) and optionally "shared_by" (list of service names).',
            ),
        ),
    ]
