import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("organizations", "0001_add_organizations_and_sso"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("deployments", "0151_managedserver_node_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="GitHubAppInstallation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("installation_id", models.BigIntegerField(unique=True)),
                ("account_login", models.CharField(max_length=255)),
                ("account_id", models.BigIntegerField()),
                (
                    "account_type",
                    models.CharField(
                        choices=[("User", "User"), ("Organization", "Organization")],
                        max_length=20,
                    ),
                ),
                (
                    "account_avatar_url",
                    models.URLField(blank=True, default=""),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("suspended", "Suspended"),
                            ("deleted", "Deleted"),
                        ],
                        default="active",
                        max_length=20,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="github_app_installations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="github_app_installations",
                        to="organizations.organization",
                    ),
                ),
                (
                    "repository_selection",
                    models.CharField(default="selected", max_length=20),
                ),
                ("repositories", models.JSONField(blank=True, default=list)),
                ("permissions", models.JSONField(blank=True, default=dict)),
                ("events", models.JSONField(blank=True, default=list)),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "suspended_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, null=True),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["user", "status"],
                        name="idx_github_install_user_status",
                    ),
                    models.Index(
                        fields=["organization", "status"],
                        name="idx_github_install_org_status",
                    ),
                    models.Index(
                        fields=["account_login"],
                        name="idx_github_install_account",
                    ),
                ],
            },
        ),
    ]
