from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0211_crowdsec_auto_unblock'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='crowdsec_first_strike_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Ban on the FIRST exploit-content probe (sensitive files, path traversal, admin-interface probing) instead of waiting for the scenario bucket to fill. Behavioral buckets (404 probing, crawlers, bruteforce) always keep their thresholds to avoid false positives.',
            ),
        ),
    ]
