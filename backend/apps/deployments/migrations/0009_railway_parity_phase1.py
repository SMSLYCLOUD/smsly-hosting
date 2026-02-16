# Generated migration for Railway Parity Phase 1
# Adds health check fields to Service model
# Expands ServiceMetric with CPU/Memory limits, Network I/O, Disk I/O

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0008_managedserver_provisioning'),
    ]

    operations = [
        # Health Check fields on Service
        migrations.AddField(
            model_name='service',
            name='health_check_path',
            field=models.CharField(
                blank=True, default='/health', help_text='HTTP path for health checks (e.g. /health, /api/health)', max_length=255),
        ),
        migrations.AddField(
            model_name='service',
            name='health_check_interval',
            field=models.IntegerField(default=30, help_text='Seconds between health checks'),
        ),
        migrations.AddField(
            model_name='service',
            name='health_check_timeout',
            field=models.IntegerField(default=5, help_text='Seconds to wait for health check response'),
        ),
        migrations.AddField(
            model_name='service',
            name='health_check_retries',
            field=models.IntegerField(default=3, help_text='Consecutive failures before marking unhealthy'),
        ),
        migrations.AddField(
            model_name='service',
            name='auto_restart',
            field=models.BooleanField(default=True, help_text='Automatically restart unhealthy containers'),
        ),
        migrations.AddField(
            model_name='service',
            name='health_status',
            field=models.CharField(
                choices=[('healthy', 'Healthy'), ('unhealthy', 'Unhealthy'), ('unknown', 'Unknown'), ('starting', 'Starting')],
                default='unknown', help_text='Current health status of the service', max_length=20),
        ),

        # Expanded ServiceMetric fields
        # Remove AutoField first
        migrations.RemoveField(
            model_name='servicemetric',
            name='id',
        ),
        migrations.AddField(
            model_name='servicemetric',
            name='id',
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='servicemetric',
            name='cpu_limit',
            field=models.DecimalField(decimal_places=4, default=1.0, help_text='CPU cores allocated', max_digits=10),
        ),
        migrations.AddField(
            model_name='servicemetric',
            name='memory_limit',
            field=models.IntegerField(default=512, help_text='Memory allocated in MB'),
        ),
        migrations.AddField(
            model_name='servicemetric',
            name='network_rx_bytes',
            field=models.BigIntegerField(default=0, help_text='Network bytes received'),
        ),
        migrations.AddField(
            model_name='servicemetric',
            name='network_tx_bytes',
            field=models.BigIntegerField(default=0, help_text='Network bytes sent'),
        ),
        migrations.AddField(
            model_name='servicemetric',
            name='disk_read_bytes',
            field=models.BigIntegerField(default=0, help_text='Disk bytes read'),
        ),
        migrations.AddField(
            model_name='servicemetric',
            name='disk_write_bytes',
            field=models.BigIntegerField(default=0, help_text='Disk bytes written'),
        ),
        # Composite index for efficient queries
        migrations.AddIndex(
            model_name='servicemetric',
            index=models.Index(fields=['service', '-timestamp'], name='deployments_svc_ts_idx'),
        ),
    ]
