from django.db import migrations, models


def backfill_auto_deploy_policy(apps, schema_editor):
    MigrationValidation = apps.get_model('deployments', 'MigrationValidation')
    for mv in MigrationValidation.objects.all():
        if mv.can_auto_deploy:
            mv.auto_deploy_policy = 'ALWAYS'
        elif mv.requires_manual_approval:
            mv.auto_deploy_policy = 'NEVER'
        else:
            mv.auto_deploy_policy = 'LOW_RISK_ONLY'
        mv.save(update_fields=['auto_deploy_policy'])


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
