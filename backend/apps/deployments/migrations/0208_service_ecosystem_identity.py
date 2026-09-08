from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("deployments", "0207_ecosystemplan_services_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="managed_by",
            field=models.CharField(
                choices=[("USER", "User"), ("ECOSYSTEM", "Ecosystem"), ("PLATFORM", "Platform")],
                db_index=True,
                default="USER",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="ecosystem_repo_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Canonical repository identity within the owning project.",
                max_length=512,
            ),
        ),
        migrations.AddIndex(
            model_name="service",
            index=models.Index(fields=["project", "ecosystem_repo_key"], name="svc_project_repo_idx"),
        ),
    ]
