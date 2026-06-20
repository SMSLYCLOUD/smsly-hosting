"""
Migration for WireGuard Mesh models.

Creates MeshNetwork and WireGuardPeer tables.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('deployments', '0035_environmentvariable_is_locked'),
    ]

    operations = [
        migrations.CreateModel(
            name='MeshNetwork',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text="e.g. 'production-mesh'", max_length=100, unique=True)),
                ('subnet', models.CharField(default='10.100.0.0/24', help_text='WireGuard subnet CIDR', max_length=50)),
                ('listen_port', models.IntegerField(default=51820)),
                ('interface_name', models.CharField(default='wg0', max_length=20)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Mesh Network',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='WireGuardPeer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('private_key', models.CharField(blank=True, default='', max_length=255)),
                ('public_key', models.CharField(blank=True, default='', max_length=255)),
                ('endpoint', models.CharField(blank=True, default='', help_text='Public IP:Port for this peer', max_length=255)),
                ('wg_address', models.GenericIPAddressField(help_text='WireGuard IP address (e.g. 10.100.0.2)', protocol='IPv4')),
                ('is_local', models.BooleanField(default=False, help_text='True if this peer is the local server')),
                ('is_active', models.BooleanField(default=True)),
                ('last_handshake', models.DateTimeField(blank=True, null=True)),
                ('latency_ms', models.FloatField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('mesh', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='peers', to='deployments.meshnetwork')),
                ('server', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='wg_peers', to='deployments.managedserver')),
            ],
            options={
                'verbose_name': 'WireGuard Peer',
                'ordering': ['wg_address'],
                'unique_together': {('mesh', 'wg_address')},
            },
        ),
    ]
