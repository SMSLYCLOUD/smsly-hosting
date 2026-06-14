from django.db import migrations, models


class Migration(migrations.Migration):
    """Add metadata JSONField to ServerTransfer.

    Stores per-transfer scratch state (e.g. pre-transfer service env vars)
    that rollback needs to read back in the same atomic step that flips
    the transfer to ROLLED_BACK.
    """

    dependencies = [
        ('deployments', '0093_backupencryptionkey'),
    ]

    operations = [
        migrations.AddField(
            model_name='servertransfer',
            name='metadata',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
