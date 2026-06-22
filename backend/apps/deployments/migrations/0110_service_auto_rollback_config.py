from django.db import migrations, models


class Migration(migrations.Migration):
    """Add auto-rollback configuration fields to Service.

    - ``auto_rollback_enabled``: per-service opt-out for the automatic
      rollback machinery. Defaults to True so existing services inherit
      the platform default.
    - ``auto_rollback_threshold``: optional per-service override of the
      global ``AUTO_ROLLBACK_THRESHOLD`` setting. ``None`` means "use the
      platform default".
    """

    dependencies = [
        ('deployments', '0109_managedserver_agent_ready'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='auto_rollback_enabled',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Allow this service to be auto-rolled-back when the '
                    'platform detects repeated failures or crash loops. '
                    'Set to False for sensitive workloads where you want '
                    'manual control.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='service',
            name='auto_rollback_threshold',
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text=(
                    'Optional per-service override for the number of '
                    'consecutive failed deployments before auto-rollback '
                    'fires. Leave blank to use the platform default '
                    '(AUTO_ROLLBACK_THRESHOLD setting).'
                ),
                null=True,
            ),
        ),
    ]