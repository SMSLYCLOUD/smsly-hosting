from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0209_deployment_is_fast_deploy'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='fast_deploy_default',
            field=models.BooleanField(
                default=False,
                help_text='Platform-wide fast deploy default: skip AI analysis and review gates, deploy straight to live. Per-service fast_deploy_enabled overrides this (True forces on, False forces off, empty inherits).',
            ),
        ),
        migrations.AddField(
            model_name='service',
            name='fast_deploy_enabled',
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text='Per-service fast deploy override: True forces fast deploys (no AI analysis, no review gates, straight to live), False forces the full review path, empty inherits the platform-wide fast_deploy_default.',
                null=True,
            ),
        ),
    ]
