"""
Add per-service CrowdSec WAF opt-out.

- disable_crowdsec_waf: when True, opt this service out of CrowdSec WAF
  protection even when the platform-level toggle is enabled.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0135_cosign_backup_security_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='disable_crowdsec_waf',
            field=models.BooleanField(
                default=False,
                help_text='Opt this service out of CrowdSec WAF protection',
            ),
        ),
    ]
