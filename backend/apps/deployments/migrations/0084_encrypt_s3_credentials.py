# Encrypt S3 credentials in BackupSchedule

from django.db import migrations
import encrypted_model_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0083_transfer_target_domain'),
    ]

    operations = [
        migrations.AlterField(
            model_name='backupschedule',
            name='s3_access_key',
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', max_length=255),
        ),
        migrations.AlterField(
            model_name='backupschedule',
            name='s3_secret_key',
            field=encrypted_model_fields.fields.EncryptedCharField(blank=True, default='', max_length=255),
        ),
    ]
