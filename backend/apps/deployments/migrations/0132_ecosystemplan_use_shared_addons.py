from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0131_enforce_device_trust'),
    ]

    operations = [
        migrations.AddField(
            model_name='ecosystemplan',
            name='use_shared_addons',
            field=models.BooleanField(
                default=True,
                help_text='When True, addons (Postgres, Redis, etc.) are provisioned once and shared across all services. When False, each service provisions its own addons independently.',
            ),
        ),
    ]
