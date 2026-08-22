from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0179_ecosystemsharedsecret'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='wildcard_redirect_custom_domain',
            field=models.BooleanField(
                blank=True,
                default=False,
                help_text=(
                    'When enabled, requests to the auto-generated wildcard domain '
                    'permanently redirect (301) to this service\'s first custom domain '
                    'instead of proxying.'
                ),
                null=True,
            ),
        ),
    ]
