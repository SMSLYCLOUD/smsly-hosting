# Generated migration for ManagedServer model
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings
import uuid
import encrypted_model_fields.fields

class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0007_platformconfig'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ManagedServer',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text="Human-readable label, e.g. 'Production VPS' or 'Staging EU'", max_length=100)),
                ('host', models.CharField(help_text="IP address or domain, e.g. '198.51.100.5' or 'prod.example.com'", max_length=255)),
                ('api_url', models.URLField(blank=True, default='', help_text='Full URL to the SMSLY Hosting API (auto-filled after provisioning)')),
                ('api_token', encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', help_text='Bearer token for the remote API (auto-filled after provisioning)', max_length=255)),
                ('ssh_port', models.IntegerField(default=22, help_text='SSH port for direct server access')),
                ('ssh_user', models.CharField(default='root', help_text='SSH username (usually root)', max_length=100)),
                ('ssh_password', encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', help_text='SSH password (encrypted at rest)', max_length=255)),
                ('ssh_key', encrypted_model_fields.fields.EncryptedTextField(blank=True, default='', help_text='SSH private key content (encrypted at rest)')),
                ('is_primary', models.BooleanField(default=False, help_text='Mark one server as the main production server')),
                ('status', models.CharField(choices=[('ONLINE', 'Online'), ('OFFLINE', 'Offline'), ('UNKNOWN', 'Unknown')], default='UNKNOWN', max_length=20)),
                ('last_health_check', models.DateTimeField(blank=True, null=True)),
                ('server_version', models.CharField(blank=True, default='', max_length=50)),
                ('services_count', models.IntegerField(default=0)),
                ('provision_status', models.CharField(choices=[('NONE', 'Not provisioned'), ('PENDING', 'Pending'), ('PROVISIONING', 'Provisioning'), ('DONE', 'Done'), ('FAILED', 'Failed')], default='NONE', max_length=20)),
                ('provision_logs', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='managed_servers', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Managed Server',
                'ordering': ['-is_primary', 'name'],
            },
        ),
    ]
