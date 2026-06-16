from django.db import migrations, models


def backfill_auto_deploy_policy(apps, schema_editor):
    """Backfill auto_deploy_policy in a single UPDATE.

    The previous N+1 implementation called save() per row, which
    issued one UPDATE per MigrationValidation row and could take
    minutes on large tables. CASE in raw SQL is a single statement
    and lets Postgres use the column-level statistics.
    """
    schema_editor.execute(
        """
        UPDATE deployments_migrationvalidation
        SET auto_deploy_policy = CASE
            WHEN can_auto_deploy = TRUE THEN 'ALWAYS'
            WHEN requires_manual_approval = TRUE THEN 'NEVER'
            ELSE 'LOW_RISK_ONLY'
        END
        """
    )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0095_service_safedeploy_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='migrationvalidation',
            name='auto_deploy_policy',
            field=models.CharField(
                choices=[
                    ('NEVER', 'Never auto-deploy (always requires approval)'),
                    ('LOW_RISK_ONLY', 'Auto-deploy for LOW risk only'),
                    ('ALWAYS', 'Auto-deploy when can_auto_deploy is True'),
                ],
                default='LOW_RISK_ONLY',
                max_length=20,
            ),
        ),
        # The AddField above runs inside the migration's transaction.
        # The backfill and the two RemoveField operations below need
        # to see the new column but can run as one statement, so we
        # keep them in the same atomic block.
        migrations.RunPython(backfill_auto_deploy_policy, reverse_code=reverse_noop),
        migrations.RemoveField(
            model_name='migrationvalidation',
            name='requires_manual_approval',
        ),
        migrations.RemoveField(
            model_name='migrationvalidation',
            name='can_auto_deploy',
        ),
    ]

