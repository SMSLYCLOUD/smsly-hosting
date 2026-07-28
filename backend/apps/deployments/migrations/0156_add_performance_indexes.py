from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0155_remove_apitoken_user_remove_auditlog_project_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="service",
            index=models.Index(fields=["status"], name="svc_status_idx"),
        ),
        migrations.AddIndex(
            model_name="service",
            index=models.Index(fields=["owner", "status"], name="svc_owner_status_idx"),
        ),
        migrations.AddIndex(
            model_name="service",
            index=models.Index(fields=["project", "status"], name="svc_project_status_idx"),
        ),
        migrations.AddIndex(
            model_name="deployment",
            index=models.Index(fields=["service", "status"], name="dep_service_status_idx"),
        ),
        migrations.AddIndex(
            model_name="deployment",
            index=models.Index(fields=["service", "-created_at"], name="dep_service_created_idx"),
        ),
        migrations.AddIndex(
            model_name="deployment",
            index=models.Index(fields=["status"], name="dep_status_idx"),
        ),
        migrations.AddIndex(
            model_name="addon",
            index=models.Index(fields=["service", "status"], name="addon_service_status_idx"),
        ),
        migrations.AddIndex(
            model_name="cronjob",
            index=models.Index(fields=["is_active", "next_run_at"], name="cron_active_next_idx"),
        ),
    ]
