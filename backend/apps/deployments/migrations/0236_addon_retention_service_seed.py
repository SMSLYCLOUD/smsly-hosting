# Generated for soft-delete retention + post-promotion seed hook.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0235_platformconfig_mesh_dns_domain'),
    ]

    operations = [
        migrations.AddField(
            model_name='addon',
            name='deleted_at',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='addon',
            name='retired_volume',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='service',
            name='seed_command',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='service',
            name='seed_last_run',
            field=models.DateTimeField(blank=True, default=None, null=True),
        ),
    ]
