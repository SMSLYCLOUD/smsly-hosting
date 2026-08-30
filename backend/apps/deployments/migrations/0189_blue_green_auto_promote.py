from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0188_mtls_enabled_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconfig',
            name='blue_green_auto_promote',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'When True, webhook / push-triggered green containers are '
                    'auto-promoted to live after BLUE_GREEN_STAGING_HOLD_SECONDS. '
                    'When False, greens stay in the staging router until manual '
                    'promotion (PaaS landing page still resolves the staging URL).'
                ),
            ),
        ),
        migrations.AddField(
            model_name='platformconfig',
            name='blue_green_staging_hold_seconds',
            field=models.PositiveIntegerField(
                default=60,
                help_text=(
                    'How long to hold a staged green container before auto-promoting '
                    'it. Set to 0 with BLUE_GREEN_AUTO_PROMOTE=0 to require manual '
                    'approval for every deploy.'
                ),
            ),
        ),
    ]
