"""
Add enforce_device_trust field to PlatformConfig.

Beta feature: when enabled, unrecognized devices must register before
accessing the platform API.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0130_trivy_scan_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='enforce_device_trust',
            field=models.BooleanField(
                default=False,
                help_text=(
                    '[Beta] When enabled, unrecognized devices must register '
                    'before accessing the platform. Requires browser fingerprint '
                    'collection on the frontend.'
                ),
            ),
        ),
    ]
