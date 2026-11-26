from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0255_encryption_opt_in"),
    ]

    operations = [
        migrations.AlterField(
            model_name="service",
            name="health_status",
            field=models.CharField(
                choices=[
                    ("healthy", "Healthy"),
                    ("unhealthy", "Unhealthy"),
                    ("standby", "Standby"),
                    ("unknown", "Unknown"),
                    ("starting", "Starting"),
                    ("needs_manual_intervention", "Needs Manual Intervention"),
                ],
                default="unknown",
                help_text="Current health status of the service",
                max_length=32,
            ),
        ),
    ]
