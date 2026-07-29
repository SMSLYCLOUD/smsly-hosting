from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0157_add_more_performance_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="managedserver",
            index=models.Index(fields=["status"], name="ms_status_idx"),
        ),
        migrations.AddIndex(
            model_name="service",
            index=models.Index(fields=["server", "status"], name="svc_server_status_idx"),
        ),
    ]
