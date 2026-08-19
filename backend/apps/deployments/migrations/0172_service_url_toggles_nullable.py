from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0171_service_url_toggles'),
    ]

    operations = [
        migrations.AlterField(
            model_name='service',
            name='wildcard_url_enabled',
            field=models.BooleanField(
                default=True, null=True, blank=True,
                help_text='Enable the master-proxied wildcard URL (e.g. service.grid.smsly.cloud)',
            ),
        ),
        migrations.AlterField(
            model_name='service',
            name='node_url_enabled',
            field=models.BooleanField(
                default=True, null=True, blank=True,
                help_text='Enable the direct node URL (e.g. service.grid-node1.smsly.cloud)',
            ),
        ),
    ]
