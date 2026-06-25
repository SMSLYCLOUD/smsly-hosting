from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0112_servicesnapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='ecosystemplan',
            name='scan_progress',
            field=models.TextField(blank=True, null=True),
        ),
    ]
