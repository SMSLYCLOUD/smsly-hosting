from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0138_add_bundle_models'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bundle',
            name='grid_addons_hash',
            field=models.CharField(
                blank=True,
                default='',
                help_text='SHA-256 of the grid.addons file at deploy time. Used to detect when a rebuild is needed.',
                max_length=128,
            ),
        ),
        migrations.AlterField(
            model_name='bundle',
            name='status',
            field=models.CharField(
                choices=[
                    ('PROVISIONING', 'Provisioning'),
                    ('ACTIVE', 'Active'),
                    ('FAILED', 'Failed'),
                    ('DELETED', 'Deleted'),
                    ('DELETION_PENDING', 'Deletion Pending'),
                    ('DELETION_FAILED', 'Deletion Failed'),
                ],
                db_index=True,
                default='PROVISIONING',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='bundlecomponent',
            name='health_status',
            field=models.CharField(
                blank=True,
                choices=[
                    ('unknown', 'Unknown'),
                    ('healthy', 'Healthy'),
                    ('unhealthy', 'Unhealthy'),
                    ('starting', 'Starting'),
                ],
                default='unknown',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='bundlecomponent',
            name='status',
            field=models.CharField(
                choices=[
                    ('PROVISIONING', 'Provisioning'),
                    ('ACTIVE', 'Active'),
                    ('FAILED', 'Failed'),
                    ('STOPPED', 'Stopped'),
                ],
                db_index=True,
                default='PROVISIONING',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='bundlebackup',
            name='component',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='backups',
                to='deployments.bundlecomponent',
            ),
        ),
    ]
