import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("deployments", "0160_rename_deployapproval_service_status_idx_deployapproval_svc_stat"),
    ]

    operations = [
        migrations.CreateModel(
            name="MtlsConfig",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        default=True,
                        help_text="Whether mTLS is enabled for this service.",
                    ),
                ),
                (
                    "trust_domain",
                    models.CharField(
                        default="platform.local",
                        help_text="SPIFFE trust domain for this service.",
                        max_length=255,
                    ),
                ),
                (
                    "spiffe_id",
                    models.CharField(
                        blank=True,
                        help_text="Auto-generated SPIFFE ID (e.g., spiffe://platform.local/service/my-app).",
                        max_length=512,
                    ),
                ),
                (
                    "svid_expiry",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the current SVID expires. Updated by the rotation task.",
                        null=True,
                    ),
                ),
                (
                    "last_rotation",
                    models.DateTimeField(
                        blank=True,
                        help_text="When the SVID was last rotated.",
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "service",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mtls_config",
                        to="deployments.service",
                    ),
                ),
            ],
            options={
                "verbose_name": "mTLS Configuration",
                "verbose_name_plural": "mTLS Configurations",
            },
        ),
    ]
