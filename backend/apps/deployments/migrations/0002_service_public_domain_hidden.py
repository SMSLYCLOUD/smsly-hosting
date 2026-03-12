from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='public_domain_hidden',
            field=models.BooleanField(default=False, help_text='When true, the auto-generated platform domain is not exposed; only custom domains serve traffic.'),
        ),
    ]
