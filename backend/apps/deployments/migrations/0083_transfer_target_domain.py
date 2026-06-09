# Add target_public_domain to ServerTransfer

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0082_cloudstorage_service_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='servertransfer',
            name='target_public_domain',
            field=models.CharField(
                blank=True, default='',
                help_text='Target platform domain for cross-platform migration (e.g., app.interserver.com)',
                max_length=500,
            ),
        ),
    ]
