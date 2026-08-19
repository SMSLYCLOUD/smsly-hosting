from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0170_add_rollback_grace_minutes'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='wildcard_url_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Enable the master-proxied wildcard URL (e.g. service.grid.smsly.cloud)',
            ),
        ),
        migrations.AddField(
            model_name='service',
            name='node_url_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Enable the direct node URL (e.g. service.grid-node1.smsly.cloud)',
            ),
        ),
    ]
