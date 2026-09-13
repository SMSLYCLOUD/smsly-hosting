from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0208_service_ecosystem_identity'),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='is_fast_deploy',
            field=models.BooleanField(
                default=False,
                help_text='Fast deploy: no AI analysis, no review gates, straight to ACTIVE',
            ),
        ),
    ]
