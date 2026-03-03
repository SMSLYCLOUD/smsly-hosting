"""
Migration for Leader Election models + ManagedServer cluster fields.

Creates ClusterState, HeartbeatLog, ElectionVote tables.
Adds role and wg_address fields to ManagedServer.
"""

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0036_meshnetwork_wireguardpeer'),
    ]

    operations = [
        # Add cluster role + wg_address to ManagedServer
        migrations.AddField(
            model_name='managedserver',
            name='role',
            field=models.CharField(
                choices=[('LEADER', 'Leader'), ('FOLLOWER', 'Follower'), ('CANDIDATE', 'Candidate')],
                default='FOLLOWER',
                help_text='Current role in the cluster (leader/follower/candidate)',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='managedserver',
            name='wg_address',
            field=models.GenericIPAddressField(
                blank=True, null=True,
                help_text='WireGuard mesh IP address (e.g. 10.100.0.2)',
                protocol='IPv4',
            ),
        ),

        # ClusterState
        migrations.CreateModel(
            name='ClusterState',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('leader_wg_address', models.GenericIPAddressField(blank=True, help_text="Leader's WireGuard IP (for fast lookup)", null=True, protocol='IPv4')),
                ('term', models.IntegerField(default=0, help_text='Current election term (monotonically increasing)')),
                ('state', models.CharField(choices=[('STABLE', 'Stable (leader active)'), ('ELECTION', 'Election in progress'), ('SPLIT_BRAIN', 'Split brain detected')], default='STABLE', max_length=20)),
                ('last_heartbeat', models.DateTimeField(blank=True, help_text='Last heartbeat received from the leader', null=True)),
                ('heartbeat_interval_ms', models.IntegerField(default=5000, help_text='How often the leader sends heartbeats (ms)')),
                ('election_timeout_ms', models.IntegerField(default=15000, help_text='How long followers wait before starting election (ms)')),
                ('min_quorum', models.IntegerField(default=2, help_text='Minimum servers needed to form quorum')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('leader', models.ForeignKey(blank=True, help_text='Current cluster leader', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='led_clusters', to='deployments.managedserver')),
                ('mesh', models.OneToOneField(blank=True, help_text='Associated mesh network (null = standalone cluster)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cluster_state', to='deployments.meshnetwork')),
            ],
            options={
                'verbose_name': 'Cluster State',
                'verbose_name_plural': 'Cluster States',
            },
        ),

        # HeartbeatLog
        migrations.CreateModel(
            name='HeartbeatLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('term', models.IntegerField(help_text='Election term when heartbeat was sent')),
                ('latency_ms', models.FloatField(blank=True, help_text='Round-trip latency in milliseconds', null=True)),
                ('success', models.BooleanField(default=True)),
                ('error_message', models.TextField(blank=True, default='')),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('cluster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='heartbeats', to='deployments.clusterstate')),
                ('source_server', models.ForeignKey(blank=True, help_text='Server that sent the heartbeat (null = local)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sent_heartbeats', to='deployments.managedserver')),
                ('target_server', models.ForeignKey(blank=True, help_text='Server that received the heartbeat (null = local)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='received_heartbeats', to='deployments.managedserver')),
            ],
            options={
                'verbose_name': 'Heartbeat Log',
                'ordering': ['-timestamp'],
                'indexes': [
                    models.Index(fields=['cluster', '-timestamp'], name='deployments_heartbe_cluster_idx'),
                ],
            },
        ),

        # ElectionVote
        migrations.CreateModel(
            name='ElectionVote',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('term', models.IntegerField()),
                ('candidate_is_local', models.BooleanField(default=False, help_text='True if voted for the local server')),
                ('voted_at', models.DateTimeField(auto_now_add=True)),
                ('cluster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='votes', to='deployments.clusterstate')),
                ('voter_server', models.ForeignKey(blank=True, help_text='Server that cast the vote (null = local)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cast_votes', to='deployments.managedserver')),
                ('candidate_server', models.ForeignKey(blank=True, help_text='Server that was voted for (null = local)', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='received_votes', to='deployments.managedserver')),
            ],
            options={
                'verbose_name': 'Election Vote',
                'ordering': ['-voted_at'],
                'unique_together': {('cluster', 'term', 'voter_server')},
            },
        ),
    ]
