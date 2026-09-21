# Addon.Status.MIGRATING for shared<->container migration concurrency guard.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0227_pooling_default_on'),
    ]

    operations = [
        migrations.AlterField(
            model_name='addon',
            name='status',
            field=models.CharField(choices=[('PROVISIONING', 'Provisioning'), ('ACTIVE', 'Active'), ('FAILED', 'Failed'), ('DELETED', 'Deleted'), ('DELETION_PENDING', 'Deletion Pending'), ('DELETION_FAILED', 'Deletion Failed'), ('MIGRATING', 'Migrating (shared <-> container)')], default='PROVISIONING', max_length=20),
        ),
    ]
