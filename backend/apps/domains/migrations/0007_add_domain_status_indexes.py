from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("domains", "0006_add_domain_service_idx"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="domain",
            index=models.Index(fields=["status"], name="domain_status_idx"),
        ),
        migrations.AddIndex(
            model_name="domain",
            index=models.Index(fields=["service", "status"], name="domain_service_status_idx"),
        ),
    ]
