from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0254_service_use_internal_network_false"),
    ]

    operations = [
        migrations.AddField(
            model_name="service",
            name="encryption_at_rest_enabled",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Opt in to grid-cryptd per-service encryption at rest (unique weekly SEK), "
                    "encrypted volumes, encrypted backups, and DB/Redis point-of-contact locking."
                ),
            ),
        ),
    ]
