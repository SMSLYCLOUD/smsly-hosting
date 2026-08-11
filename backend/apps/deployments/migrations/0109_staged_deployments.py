"""Add STAGED status, staging_url/staged_at to Deployment, staging_domain to Service."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0161_alter_service_autoscale_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='staging_url',
            field=models.URLField(
                blank=True, null=True,
                help_text='Temporary staging URL where the green container can be previewed before promote',
            ),
        ),
        migrations.AddField(
            model_name='deployment',
            name='staged_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='When the deployment entered STAGED status (auto-promote timer starts)',
            ),
        ),
        migrations.AddField(
            model_name='service',
            name='staging_domain',
            field=models.CharField(
                blank=True, max_length=255, null=True,
                help_text='Custom staging domain for webhook deployments (e.g. staging.example.com). '
                          'If blank, auto-generated from service name + base domain.',
            ),
        ),
    ]
