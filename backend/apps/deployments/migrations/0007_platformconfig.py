# Generated migration for PlatformConfig model
import encrypted_model_fields.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0006_alter_service_max_replicas_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlatformConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.CharField(blank=True, default='', help_text='Primary domain (e.g. cloud.smsly.cloud)', max_length=255)),
                ('use_ssl', models.BooleanField(default=False, help_text="Enable HTTPS via Let's Encrypt")),
                ('cloudflare_api_token', encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', help_text='Cloudflare API Token for DNS challenge (Edit zone DNS)', max_length=255)),
                ('wildcard_subdomains', models.BooleanField(default=True, help_text='Enable wildcard SSL for *.domain deployed services')),
                ('server_ip', models.GenericIPAddressField(blank=True, help_text='Server public IP (auto-detected or manual)', null=True)),
                ('caddy_status', models.CharField(default='unknown', help_text='Last known Caddy status', max_length=20)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Platform Configuration',
                'verbose_name_plural': 'Platform Configuration',
            },
        ),
    ]
