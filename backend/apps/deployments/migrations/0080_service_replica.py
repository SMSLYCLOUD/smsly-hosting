# ServiceReplica model for auto-scaling

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0079_transfer_source_ssh'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceReplica',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('container_name', models.CharField(max_length=255)),
                ('container_id', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('SPAWNING', 'Spawning'), ('RUNNING', 'Running'), ('DRAINING', 'Draining'), ('DESTROYING', 'Destroying'), ('DESTROYED', 'Destroyed')], default='SPAWNING', max_length=20)),
                ('metrics_snapshot', models.JSONField(default=dict, help_text='Last known CPU/mem from Prometheus')),
                ('spawn_reason', models.CharField(blank=True, default='', max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('destroyed_at', models.DateTimeField(blank=True, null=True)),
                ('node', models.ForeignKey(help_text='The remote node where this replica runs', null=True, on_delete=models.SET_NULL, related_name='hosted_replicas', to='deployments.managedserver')),
                ('service', models.ForeignKey(on_delete=models.CASCADE, related_name='replicas', to='deployments.service')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['service', 'status'], name='replica_service_status_idx'),
                    models.Index(fields=['node', 'status'], name='replica_node_status_idx'),
                ],
            },
        ),
    ]
