# Generated for cancel->revoke: store the celery task id so cancelling
# a deployment also stops its worker (DB flag alone leaves delivered
# tasks running -> duplicate promotes collided on canonical names).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0236_addon_retention_service_seed'),
    ]

    operations = [
        migrations.AddField(
            model_name='deployment',
            name='celery_task_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
