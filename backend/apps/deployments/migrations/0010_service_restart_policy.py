# Generated migration for Phase 3: restart_policy field on Service

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0009_railway_parity_phase1'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='restart_policy',
            field=models.CharField(
                choices=[
                    ('always', 'Always'),
                    ('unless-stopped', 'Unless Stopped'),
                    ('on-failure', 'On Failure'),
                    ('no', 'Never'),
                ],
                default='unless-stopped',
                help_text='Docker restart policy for the container',
                max_length=20,
            ),
        ),
    ]
