import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mtls", "0003_alter_mtlsconfig_id_alter_mtlsconfig_trust_domain"),
    ]

    operations = [
        migrations.CreateModel(
            name="MtlsAuthorizationPolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="Human-readable policy name.",
                        max_length=255,
                    ),
                ),
                (
                    "source_spiffe_id",
                    models.CharField(
                        help_text=(
                            "SPIFFE ID of the caller. Use '*' for any source. "
                            "Example: spiffe://ecosystem.local/service/frontend"
                        ),
                        max_length=512,
                    ),
                ),
                (
                    "paths",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            'Path prefixes this policy applies to. Empty = all paths. '
                            'Example: ["/api/", "/internal/"]'
                        ),
                    ),
                ),
                (
                    "methods",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text=(
                            'HTTP methods this policy applies to. Empty = all methods. '
                            'Example: ["GET", "POST"]'
                        ),
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("allow", "Allow"),
                            ("deny", "Deny"),
                        ],
                        default="allow",
                        max_length=10,
                    ),
                ),
                (
                    "priority",
                    models.IntegerField(
                        default=0,
                        help_text="Higher priority rules are evaluated first.",
                    ),
                ),
                (
                    "enabled",
                    models.BooleanField(
                        default=True,
                        help_text="Whether this policy is active.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "target_service",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mtls_inbound_policies",
                        to="deployments.service",
                    ),
                ),
            ],
            options={
                "verbose_name": "mTLS Authorization Policy",
                "verbose_name_plural": "mTLS Authorization Policies",
                "ordering": ["-priority", "id"],
            },
        ),
    ]
