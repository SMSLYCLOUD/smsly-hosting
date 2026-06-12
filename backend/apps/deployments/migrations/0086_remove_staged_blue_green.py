# Generated manually — removes STAGED blue-green dead code.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0085_rename_replica_service_status_idx_deployments_service_8a4250_idx_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='deployment',
            name='staged_at',
        ),
        migrations.AlterField(
            model_name='deployment',
            name='status',
            field=models.CharField(
                choices=[
                    ('QUEUED', 'Queued'),
                    ('REVIEW', 'Review'),
                    ('BUILDING', 'Building'),
                    ('BUILD_FAILED', 'Build Failed'),
                    ('AWAITING_APPROVAL', 'Awaiting Approval'),
                    ('BACKUP_RUNNING', 'Backup Running'),
                    ('BACKUP_FAILED', 'Backup Failed'),
                    ('MIGRATION_PLANNING', 'Migration Planning'),
                    ('MIGRATION_RUNNING', 'Migration Running'),
                    ('MIGRATION_FAILED', 'Migration Failed'),
                    ('DEPLOYING', 'Deploying'),
                    ('HEALTH_CHECK', 'Health Check'),
                    ('ACTIVE', 'Active'),
                    ('FAILED', 'Failed'),
                    ('CANCELLED', 'Cancelled'),
                    ('INACTIVE', 'Inactive'),
                    ('ROLLING_BACK', 'Rolling Back'),
                    ('ROLLED_BACK', 'Rolled Back'),
                ],
                default='QUEUED',
                max_length=20,
            ),
        ),
    ]
