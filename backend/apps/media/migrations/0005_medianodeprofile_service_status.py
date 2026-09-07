from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0004_medianodeprofile_script_repo_token_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="medianodeprofile",
            name="service_status",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
