# Generated migration: backup restore bookkeeping + ServerBackup type.

# Fixes two crash bugs found in the cloud-storage/backup review:
#
# 1. core.py wrote backup.restored_at / backup.restore_count via
#    save(update_fields=[...]) on BOTH ServiceBackup and ServerBackup,
#    but neither model had the fields — every successful restore ended
#    with a FieldError AFTER the restore work completed.
# 2. backup_server() created ServerBackup rows with
#    backup_type='SERVER', but ServerBackup had no backup_type field —
#    every server backup crashed at row creation (TypeError).
#    The field now also carries SERVER_TRANSFER for full-server
#    transfers that include real secret values.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0197_service_ha_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicebackup',
            name='restored_at',
            field=models.DateTimeField(blank=True, null=True,
                help_text='Last successful restore completion time.'),
        ),
        migrations.AddField(
            model_name='servicebackup',
            name='restore_count',
            field=models.PositiveIntegerField(default=0,
                help_text='Number of successful restores from this backup.'),
        ),
        migrations.AddField(
            model_name='serverbackup',
            name='backup_type',
            field=models.CharField(choices=[
                ('SERVER', 'Server'),
                ('SERVER_TRANSFER', 'Server Transfer'),
            ], default='SERVER', max_length=20,
            help_text='SERVER = normal backup (secrets masked). '
                      'SERVER_TRANSFER = includes real secret values '
                      'for hydrating a target master during full-server '
                      'transfer.'),
        ),
        migrations.AddField(
            model_name='serverbackup',
            name='restored_at',
            field=models.DateTimeField(blank=True, null=True,
                help_text='Last successful restore completion time.'),
        ),
        migrations.AddField(
            model_name='serverbackup',
            name='restore_count',
            field=models.PositiveIntegerField(default=0,
                help_text='Number of successful restores from this backup.'),
        ),
    ]
