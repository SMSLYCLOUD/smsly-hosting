from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0004_add_replication_event_types"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="notification",
            index=models.Index(fields=["user", "read", "-created_at"], name="notif_user_read_created_idx"),
        ),
        migrations.AddIndex(
            model_name="resourcealert",
            index=models.Index(fields=["service", "acknowledged", "-created_at"], name="resalert_svc_ack_created_idx"),
        ),
    ]
