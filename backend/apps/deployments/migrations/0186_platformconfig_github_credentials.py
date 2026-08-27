from django.db import migrations, models
import apps.deployments.models.platform


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0185_addon_ha"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformconfig",
            name="github_app_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "GitHub App numeric ID. Set via setup_github — "
                    "falls back to GITHUB_APP_ID env var if empty."
                ),
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="github_app_private_key",
            field=apps.deployments.models.platform.EncryptedCharField(
                blank=True,
                default="",
                help_text=(
                    "GitHub App private key PEM. Set via setup_github — "
                    "falls back to GITHUB_APP_PRIVATE_KEY env var if empty."
                ),
                max_length=8192,
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="github_client_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="GitHub OAuth App Client ID. Falls back to GITHUB_CLIENT_ID env var.",
                max_length=128,
            ),
        ),
        migrations.AddField(
            model_name="platformconfig",
            name="github_client_secret",
            field=apps.deployments.models.platform.EncryptedCharField(
                blank=True,
                default="",
                help_text="GitHub OAuth App Client Secret. Falls back to GITHUB_CLIENT_SECRET env var.",
                max_length=512,
            ),
        ),
    ]
