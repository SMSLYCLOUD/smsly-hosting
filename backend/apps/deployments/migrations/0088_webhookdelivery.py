import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds the WebhookDelivery table used for at-most-once webhook processing.
    """

    dependencies = [
        ('deployments', '0087_health_manual_intervention_and_queued_replicas'),
    ]

    operations = [
        migrations.CreateModel(
            name='WebhookDelivery',
            fields=[
                ('delivery_id', models.CharField(
                    help_text='Provider-supplied unique delivery identifier (e.g. X-GitHub-Delivery).',
                    max_length=128,
                    primary_key=True,
                    serialize=False,
                )),
                ('provider', models.CharField(
                    default='github',
                    help_text='Webhook provider that produced this delivery.',
                    max_length=32,
                )),
                ('event_type', models.CharField(
                    blank=True,
                    default='',
                    help_text='Event type from the provider (push, pull_request, etc.).',
                    max_length=64,
                )),
                ('received_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('status', models.CharField(
                    choices=[
                        ('processed', 'Processed'),
                        ('failed', 'Failed'),
                        ('ignored', 'Ignored'),
                    ],
                    default='processed',
                    max_length=16,
                )),
            ],
            options={
                'indexes': [
                    models.Index(
                        fields=['provider', 'received_at'],
                        name='deployments_prov_idx',
                    ),
                ],
            },
        ),
    ]
