from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0210_fast_deploy_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='crowdsec_auto_unblock_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Automatically remove CrowdSec bans older than crowdsec_auto_unblock_after_hours. Turn off to require manual unblock for every ban.',
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='crowdsec_auto_unblock_after_hours',
            field=models.PositiveIntegerField(
                default=24,
                help_text='Bans older than this many hours are auto-removed by the periodic sweeper (only when auto-unblock is enabled).',
            ),
        ),
    ]
