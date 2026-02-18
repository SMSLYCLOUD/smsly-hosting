# Generated migration for pre-deploy review gate

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0018_add_qdrant_addon_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='deployment',
            name='status',
            field=models.CharField(
                choices=[
                    ('QUEUED', 'Queued'),
                    ('REVIEW', 'Review'),
                    ('BUILDING', 'Building'),
                    ('DEPLOYING', 'Deploying'),
                    ('HEALTH_CHECK', 'Health Check'),
                    ('ACTIVE', 'Active'),
                    ('FAILED', 'Failed'),
                    ('CANCELLED', 'Cancelled'),
                ],
                default='QUEUED',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='deployment',
            name='review_summary',
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='AI-recommended resources, env vars, and issues for review',
            ),
        ),
    ]
