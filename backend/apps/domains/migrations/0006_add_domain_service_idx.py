from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("domains", "0005_add_checked_at_ssl_fail_count"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="domain",
            index=models.Index(fields=["service"], name="domain_service_idx"),
        ),
    ]
