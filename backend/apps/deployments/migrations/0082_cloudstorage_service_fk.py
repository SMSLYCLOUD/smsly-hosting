# Add service FK to CloudStorageDestination

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0081_cloud_storage_destination'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudstoragedestination',
            name='service',
            field=models.ForeignKey(
                blank=True,
                help_text='NULL = platform-wide; set = per-service only',
                null=True,
                on_delete=models.CASCADE,
                related_name='cloud_destinations',
                to='deployments.service',
            ),
        ),
    ]
