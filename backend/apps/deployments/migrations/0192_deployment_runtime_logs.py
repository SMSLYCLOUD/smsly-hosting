# Generated migration: add Deployment.runtime_logs so build output and
# runtime output are stored (and surfaced in the UI) separately.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0191_internal_network_alignment'),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='runtime_logs',
            field=models.TextField(blank=True, default=''),
        ),
    ]
