from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0156_add_performance_indexes"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="managedserver",
            index=models.Index(fields=["is_primary", "status"], name="ms_primary_status_idx"),
        ),
        migrations.AddIndex(
            model_name="managedserver",
            index=models.Index(fields=["owner"], name="ms_owner_idx"),
        ),
        migrations.AddIndex(
            model_name="previewenvironment",
            index=models.Index(fields=["service", "status"], name="preview_service_status_idx"),
        ),
        migrations.AddIndex(
            model_name="previewenvironment",
            index=models.Index(fields=["service", "-created_at"], name="preview_service_created_idx"),
        ),
        migrations.AddIndex(
            model_name="deploymentapproval",
            index=models.Index(fields=["service", "status"], name="deployapproval_service_status_idx"),
        ),
        migrations.AddIndex(
            model_name="platformupdate",
            index=models.Index(fields=["status"], name="platupdate_status_idx"),
        ),
        migrations.AddIndex(
            model_name="platformupdate",
            index=models.Index(fields=["-created_at"], name="platupdate_created_idx"),
        ),
        migrations.AddIndex(
            model_name="servertransfer",
            index=models.Index(fields=["status"], name="srvtransfer_status_idx"),
        ),
        migrations.AddIndex(
            model_name="servertransfer",
            index=models.Index(fields=["owner", "status"], name="srvtransfer_owner_status_idx"),
        ),
    ]
