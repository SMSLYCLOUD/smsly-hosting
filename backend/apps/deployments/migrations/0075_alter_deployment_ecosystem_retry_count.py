from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0074_deployment_ecosystem_retry_count'),
    ]

    operations = [
        migrations.AlterField(
            model_name='deployment',
            name='ecosystem_retry_count',
            field=models.IntegerField(
                db_default=0,
                default=0,
                help_text='Number of times ecosystem deploy has retried this deployment',
            ),
        ),
    ]
