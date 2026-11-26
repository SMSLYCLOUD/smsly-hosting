# Generated manually on 2026-11-25 for Addon.Status.STOPPED (on-demand parking)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0258_reservedsubdomain_partial_unique"),
    ]

    operations = [
        migrations.AlterField(
            model_name="addon",
            name="status",
            field=models.CharField(
                choices=[
                    ("PROVISIONING", "Provisioning"),
                    ("ACTIVE", "Active"),
                    ("STOPPED", "Stopped (Parked on demand)"),
                    ("FAILED", "Failed"),
                    ("BACKEND_MISSING", "Backend missing (row kept, needs reprovision)"),
                    ("DELETED", "Deleted"),
                    ("DELETION_PENDING", "Deletion Pending"),
                    ("DELETION_FAILED", "Deletion Failed"),
                    ("MIGRATING", "Migrating (shared <-> container)"),
                ],
                default="PROVISIONING",
                max_length=20,
            ),
        ),
    ]
