from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('deployments', '0097_reservedsubdomain_released_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='service',
            name='health_webhook_token_last_used_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='Last time the health webhook token was used (for idle expiry).',
            ),
        ),
    ]
