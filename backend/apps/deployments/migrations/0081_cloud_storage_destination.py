# CloudStorageDestination model

import uuid

import encrypted_model_fields.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0080_service_replica'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudStorageDestination',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200)),
                ('provider', models.CharField(choices=[('r2', 'Cloudflare R2'), ('s3', 'Amazon S3'), ('minio', 'MinIO / Self-Hosted S3'), ('b2', 'Backblaze B2 (S3-compatible)'), ('digitalocean', 'DigitalOcean Spaces'), ('vps', 'Custom Storage VPS'), ('wasabi', 'Wasabi Hot Storage')], default='s3', max_length=30)),
                ('bucket', models.CharField(max_length=255)),
                ('region', models.CharField(default='us-east-1', max_length=100)),
                ('endpoint', models.CharField(blank=True, default='', help_text='Custom endpoint for R2/MinIO/B2. Leave blank for AWS S3.', max_length=500)),
                ('access_key', encrypted_model_fields.fields.EncryptedCharField(max_length=255)),
                ('secret_key', encrypted_model_fields.fields.EncryptedCharField(max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]
