from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cloud", "0003_backupencryptionkey_backupschedule_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="servicebackup",
            index=models.Index(fields=["service", "status"], name="svcbackup_service_status_idx"),
        ),
        migrations.AddIndex(
            model_name="servicebackup",
            index=models.Index(fields=["service", "-created_at"], name="svcbackup_service_created_idx"),
        ),
        migrations.AddIndex(
            model_name="serverbackup",
            index=models.Index(fields=["status"], name="srvbackup_status_idx"),
        ),
        migrations.AddIndex(
            model_name="serverbackup",
            index=models.Index(fields=["-created_at"], name="srvbackup_created_idx"),
        ),
        migrations.AddIndex(
            model_name="backupschedule",
            index=models.Index(fields=["service", "enabled"], name="bkupsched_service_enabled_idx"),
        ),
    ]
