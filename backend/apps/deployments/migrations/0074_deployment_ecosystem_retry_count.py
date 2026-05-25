from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0073_deploymentapproval_rejected_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='ecosystem_retry_count',
            field=models.IntegerField(
                default=0,
                help_text='Number of times ecosystem deploy has retried this deployment',
            ),
        ),
    ]
