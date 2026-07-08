import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0137_platformconfig_mapbox_token'),
    ]

    operations = [
        migrations.CreateModel(
            name='Bundle',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text="Bundle name from grid.addons (e.g. 'sip-stack')", max_length=255)),
                ('network', models.CharField(blank=True, default='', help_text='Docker network name for the bundle', max_length=255)),
                ('status', models.CharField(choices=[('PROVISIONING', 'Provisioning'), ('ACTIVE', 'Active'), ('FAILED', 'Failed'), ('DELETED', 'Deleted'), ('DELETION_PENDING', 'Deletion Pending'), ('DELETION_FAILED', 'Deletion Failed')], default='PROVISIONING', max_length=20)),
                ('grid_addons_hash', models.CharField(blank=True, default='', help_text='SHA-256 of the grid.addons file at deploy time. Used to detect when a rebuild is needed.', max_length=64)),
                ('deletion_error', models.TextField(blank=True, default='')),
                ('service', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bundles', to='deployments.service')),
            ],
            options={
                'ordering': ['name'],
                'unique_together': {('service', 'name')},
            },
        ),
        migrations.CreateModel(
            name='BundleComponent',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text="Component name from grid.addons (e.g. 'kamailio')", max_length=255)),
                ('source_type', models.CharField(choices=[('IMAGE', 'Docker Image'), ('REPO', 'Git Repository')], default='IMAGE', max_length=10)),
                ('image', models.CharField(blank=True, default='', help_text='Docker image (for IMAGE source type)', max_length=512)),
                ('repo', models.CharField(blank=True, default='', help_text='Git repo URL (for REPO source type)', max_length=512)),
                ('branch', models.CharField(blank=True, default='main', max_length=255)),
                ('build_type', models.CharField(blank=True, default='', help_text="'dockerfile' or 'nixpacks'", max_length=20)),
                ('status', models.CharField(choices=[('PROVISIONING', 'Provisioning'), ('ACTIVE', 'Active'), ('FAILED', 'Failed'), ('STOPPED', 'Stopped')], default='PROVISIONING', max_length=20)),
                ('container_name', models.CharField(blank=True, default='', help_text='Docker container name', max_length=255)),
                ('container_id', models.CharField(blank=True, default='', help_text='Short Docker container ID', max_length=64)),
                ('connection_url', models.CharField(blank=True, default='', help_text='Connection URL for this component', max_length=512)),
                ('ports', models.JSONField(blank=True, default=list, help_text='Port mappings from grid.addons')),
                ('health_status', models.CharField(blank=True, default='unknown', max_length=20)),
                ('bundle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='components', to='deployments.bundle')),
            ],
            options={
                'ordering': ['name'],
                'unique_together': {('bundle', 'name')},
            },
        ),
        migrations.CreateModel(
            name='BundleBackup',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('file_path', models.CharField(blank=True, max_length=512)),
                ('size_bytes', models.BigIntegerField(default=0)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed')], default='PENDING', max_length=20)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('error_message', models.TextField(blank=True)),
                ('component', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='backups', to='deployments.bundlecomponent')),
            ],
        ),
    ]
