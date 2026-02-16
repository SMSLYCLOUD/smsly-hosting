# Generated migration for custom_domains JSONField

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0010_service_restart_policy'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='custom_domains',
            field=models.JSONField(
                blank=True, default=list,
                help_text='List of custom domains attached to this service'),
        ),
    ]
