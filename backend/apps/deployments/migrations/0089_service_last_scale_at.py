# Add last_scale_at to Service for autoscaler cooldown tracking.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0088_webhookdelivery'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='last_scale_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Last time the autoscaler scaled this service (used for cooldown).',
            ),
        ),
    ]
