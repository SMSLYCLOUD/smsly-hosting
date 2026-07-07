"""
Add AlertRule and NotificationChannel models.
"""
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationChannel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(help_text='Friendly name for this channel', max_length=100)),
                ('channel_type', models.CharField(choices=[('email', 'Email'), ('slack', 'Slack Webhook'), ('sms', 'SMS'), ('webhook', 'Generic Webhook')], max_length=20)),
                ('target', models.CharField(help_text='Email address, Slack webhook URL, phone number, or generic webhook URL', max_length=500)),
                ('enabled', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='AlertRule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('enabled', models.BooleanField(default=True)),
                ('metric', models.CharField(choices=[('cpu', 'CPU Usage'), ('memory', 'Memory Usage'), ('disk', 'Disk Usage'), ('status', 'Service Status'), ('response_time', 'Response Time'), ('error_rate', 'Error Rate')], max_length=30)),
                ('operator', models.CharField(choices=[('>', 'Greater than'), ('>=', 'Greater than or equal'), ('<', 'Less than'), ('<=', 'Less than or equal'), ('==', 'Equal to'), ('!=', 'Not equal to')], default='>', max_length=5)),
                ('threshold', models.FloatField(help_text='Threshold value for the metric')),
                ('severity', models.CharField(choices=[('info', 'Info'), ('warning', 'Warning'), ('critical', 'Critical')], default='warning', max_length=10)),
                ('cooldown_minutes', models.PositiveIntegerField(default=5, help_text='Minimum minutes between repeated notifications for this rule')),
                ('message_template', models.TextField(blank=True, default='', help_text='Custom alert message template. Use {metric}, {value}, {threshold}, {service} as placeholders.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('channels', models.ManyToManyField(blank=True, related_name='alert_rules', to='notifications.notificationchannel')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
    ]
