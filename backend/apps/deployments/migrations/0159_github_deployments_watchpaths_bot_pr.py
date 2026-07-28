"""Add GitHub Deployments API support, monorepo watch paths, and bot PR handling."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("deployments", "0158_add_server_domain_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="github_deployment_id",
            field=models.BigIntegerField(
                blank=True,
                help_text="GitHub Deployment ID for status updates via the Deployments API",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="watch_paths",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Glob patterns for monorepo path filtering. Empty list = deploy on any file change.",
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="bot_pr_strategy",
            field=models.CharField(
                choices=[
                    ("DEPLOY", "Deploy"),
                    ("SKIP", "Skip"),
                    ("COMMENT_ONLY", "Comment Only"),
                ],
                default="DEPLOY",
                help_text="How to handle PRs from bots (Dependabot, Renovate, etc.)",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="service",
            name="last_pr_comment_id",
            field=models.BigIntegerField(
                blank=True,
                help_text="GitHub comment ID for the most recent PR preview comment",
                null=True,
            ),
        ),
    ]
