# Generated for stable mesh addressing of suffixed services.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0237_deployment_celery_task_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='mesh_alias',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
