from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0184_default_project_for_orphan_services'),
    ]

    operations = [
        migrations.AddField(
            model_name='addon',
            name='ha_enabled',
            field=models.BooleanField(default=False, help_text='Whether automatic-failover replication is enabled for this addon.'),
        ),
        migrations.AddField(
            model_name='addon',
            name='ha_status',
            field=models.CharField(choices=[('DISABLED', 'HA Disabled'), ('ENABLING', 'Enabling HA'), ('HEALTHY', 'HA Healthy'), ('DEGRADED', 'HA Degraded'), ('FAILED_OVER', 'Failed Over'), ('FAILED', 'HA Failed'), ('DISABLING', 'Disabling HA')], default='DISABLED', max_length=20),
        ),
        migrations.AddField(
            model_name='addon',
            name='replica_container_name',
            field=models.CharField(blank=True, default='', help_text='Container name of the standby/replica (empty when HA is off).', max_length=255),
        ),
        migrations.AddField(
            model_name='addon',
            name='ha_topology',
            field=models.JSONField(blank=True, default=dict, help_text='HA component inventory: sentinel hosts, sidecar, monitor, mode.'),
        ),
    ]
