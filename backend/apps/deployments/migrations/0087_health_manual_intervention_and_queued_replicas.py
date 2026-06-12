from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds the ``needs_manual_intervention`` value to ``Service.health_status``
    (used when auto-restart cannot find a successful deployment to fall back
    to) and the ``queued_min_replicas`` snapshot column on ``Deployment`` so
    the deploy executor uses the replica count that was valid at queue time.
    """

    dependencies = [
        ('deployments', '0086_remove_staged_blue_green'),
    ]

    operations = [
        migrations.AlterField(
            model_name='service',
            name='health_status',
            field=models.CharField(
                choices=[
                    ('healthy', 'Healthy'),
                    ('unhealthy', 'Unhealthy'),
                    ('unknown', 'Unknown'),
                    ('starting', 'Starting'),
                    ('needs_manual_intervention', 'Needs Manual Intervention'),
                ],
                default='unknown',
                help_text='Current health status of the service',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='deployment',
            name='queued_min_replicas',
            field=models.IntegerField(
                blank=True,
                help_text='Snapshot of service.min_replicas captured at queue time so the deploy '
                          'executor uses the original replica count even if the autoscaler mutates '
                          'it during the build.',
                null=True,
            ),
        ),
    ]
