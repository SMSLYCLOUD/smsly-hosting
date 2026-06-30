# Add cloud_upload_enabled to BackupSchedule and SnapshotSchedule

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0124_backup_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='backupschedule',
            name='cloud_upload_enabled',
            field=models.BooleanField(
                default=True,
                help_text='When True, automatically upload backups to the configured cloud destination. Turn off to keep backups local-only even with credentials set.',
            ),
        ),
        migrations.AddField(
            model_name='snapshotschedule',
            name='cloud_upload_enabled',
            field=models.BooleanField(
                default=True,
                help_text='When True, automatically upload snapshots to the configured cloud destination. Turn off to keep snapshots local-only even with credentials set.',
            ),
        ),
    ]
