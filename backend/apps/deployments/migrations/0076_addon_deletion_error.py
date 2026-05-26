from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0075_alter_deployment_ecosystem_retry_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='addon',
            name='deletion_error',
            field=models.TextField(blank=True, default=''),
        ),
    ]
