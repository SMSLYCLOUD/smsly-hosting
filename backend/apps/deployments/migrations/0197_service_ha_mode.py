# Generated migration: Service HA configuration fields.

# Adds per-service HA mode (local vs remote) and the PlatformConfig
# master toggle. The ServiceHAManager reads these to decide whether
# a service participates in HA failover and which failover strategy
# to use:
#
#   ha_mode = 'none'     → no HA (default — behaves exactly as before)
#   ha_mode = 'local'    → LOCAL HA: multiple container replicas on
#                           the SAME node (Docker restart policy +
#                           health monitor + container auto-restart).
#                           Fast failover (<5s), zero network changes.
#   ha_mode = 'remote'    → REMOTE HA: replica on a DIFFERENT node.
#                           Survives node-level failures (disk, kernel,
#                           network partition, power). Slower failover
#                           (30-120s), needs image on the target node.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0196_add_media_repo_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='ha_mode',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('none', 'No HA'),
                    ('local', 'Local HA (same-node replicas)'),
                    ('remote', 'Remote HA (cross-node failover)'),
                ],
                default='none',
                help_text=(
                    'High-availability mode. none = no HA. local = '
                    'multiple replicas on the same node (fast failover). '
                    'remote = replica on a different node (survives node '
                    'failure).'
                ),
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='service_ha_enabled',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Master toggle for the Service HA manager (beat task). '
                    'When enabled, the HA pass evaluates services with '
                    'ha_mode != none and performs replica/node failovers. '
                    'Individual services opt in via their ha_mode field.'
                ),
            ),
        ),
    ]
