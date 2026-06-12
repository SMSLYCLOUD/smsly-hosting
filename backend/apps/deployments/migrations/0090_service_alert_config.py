# Add alert_config JSONField to Service for autoscaler alert thresholds.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0089_service_last_scale_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='alert_config',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Per-service autoscaler alert thresholds and notification targets.',
            ),
        ),
    ]
