# Generated migration for S3 backup storage backend fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0077_widen_env_var_value'),
    ]

    operations = [
        migrations.AddField(
            model_name='backupschedule',
            name='s3_access_key',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='backupschedule',
            name='s3_bucket',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='backupschedule',
            name='s3_endpoint',
            field=models.CharField(blank=True, default='', help_text='Custom endpoint for R2/MinIO. Leave blank for AWS S3.', max_length=500),
        ),
        migrations.AddField(
            model_name='backupschedule',
            name='s3_region',
            field=models.CharField(blank=True, default='us-east-1', max_length=100),
        ),
        migrations.AddField(
            model_name='backupschedule',
            name='s3_secret_key',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='backupschedule',
            name='storage_backend',
            field=models.CharField(choices=[('local', 'Local'), ('s3', 'S3 / R2 / MinIO')], default='local', max_length=20),
        ),
    ]
