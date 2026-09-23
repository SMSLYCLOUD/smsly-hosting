from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("domains", "0007_add_domain_status_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="domain",
            name="verification_token",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
