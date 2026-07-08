"""Add ServiceTrafficLog model."""
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0135_cosign_backup_security_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServiceTrafficLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('ip_address', models.GenericIPAddressField()),
                ('domain', models.CharField(blank=True, default='', max_length=255)),
                ('country_code', models.CharField(blank=True, default='', max_length=2)),
                ('country_name', models.CharField(blank=True, default='', max_length=100)),
                ('city', models.CharField(blank=True, default='', max_length=200)),
                ('latitude', models.FloatField(blank=True, null=True)),
                ('longitude', models.FloatField(blank=True, null=True)),
                ('request_count', models.PositiveIntegerField(default=1)),
                ('last_seen', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('geo_resolved', models.BooleanField(default=False)),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='traffic_logs', to='deployments.service')),
            ],
            options={
                'unique_together': {('service', 'ip_address', 'domain')},
            },
        ),
        migrations.AddIndex(
            model_name='servicetrafficlog',
            index=models.Index(fields=['service', 'country_code'], name='deployments_service_country_code_idx'),
        ),
        migrations.AddIndex(
            model_name='servicetrafficlog',
            index=models.Index(fields=['ip_address'], name='deployments_ip_address_idx'),
        ),
        migrations.AddIndex(
            model_name='servicetrafficlog',
            index=models.Index(fields=['geo_resolved'], name='deployments_geo_resolved_idx'),
        ),
        migrations.AddIndex(
            model_name='servicetrafficlog',
            index=models.Index(fields=['-last_seen'], name='deployments_last_seen_idx'),
        ),
    ]
