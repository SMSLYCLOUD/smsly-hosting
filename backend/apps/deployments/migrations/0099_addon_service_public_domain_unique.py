from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0098_service_health_webhook_token_last_used_at'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='addon',
            constraint=models.UniqueConstraint(
                fields=('service', 'public_domain'),
                name='uniq_addon_service_public_domain',
            ),
        ),
    ]
