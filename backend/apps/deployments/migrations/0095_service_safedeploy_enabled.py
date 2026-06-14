from django.db import migrations, models


class Migration(migrations.Migration):
    """Add safedeploy_enabled to Service and metadata scratch field to Deployment.

    The Service flag gates whether production deploys are routed through the
    SafeDeploy pipeline (preview -> migration validation -> risk classification
    -> manual approval). The Deployment.metadata JSONField stores per-pipeline
    scratch state, e.g. the captured pre-migration state needed by the
    automatic rollback in `_run_migration_phase`.
    """

    dependencies = [
        ('deployments', '0094_servertransfer_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='safedeploy_enabled',
            field=models.BooleanField(
                default=False,
                help_text="When true, production deploys go through the SafeDeploy pipeline (preview → migration validation → risk classification → manual approval).",
            ),
        ),
        migrations.AddField(
            model_name='deployment',
            name='metadata',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Scratch state for in-flight pipeline phases (e.g. pre-migration state for rollback).",
            ),
        ),
    ]
