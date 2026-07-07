import uuid

from django.conf import settings
from django.db import models

from apps.deployments.models import Service  # type: ignore[attr-defined]


class NotificationPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    event_type = models.CharField(choices=[  # type: ignore[var-annotated]
        ('deploy_success', 'Deploy Success'),
        ('deploy_failed', 'Deploy Failed'),
        ('health_alert', 'Health Alert'),
        ('billing_due', 'Billing Due'),
        ('ssl_expiring', 'SSL Expiring'),
        ('backup_completed', 'Backup Completed'),
    ], max_length=50)
    channels = models.JSONField(default=list)  # type: ignore[var-annotated] # ['email', 'webhook', 'in_app']

    class Meta:
        unique_together = ('user', 'event_type')

class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)  # type: ignore[var-annotated]
    title = models.CharField(max_length=200)  # type: ignore[var-annotated]
    message = models.TextField()  # type: ignore[var-annotated]
    event_type = models.CharField(max_length=50)  # type: ignore[var-annotated]
    read = models.BooleanField(default=False)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    class Meta:
        ordering = ['-created_at']

class ResourceAlert(models.Model):
    """Tracks resource usage alerts for services."""
    class Severity(models.TextChoices):
        INFO = 'INFO', 'Info'
        WARNING = 'WARNING', 'Warning'
        CRITICAL = 'CRITICAL', 'Critical'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service, on_delete=models.CASCADE, related_name='resource_alerts')
    severity = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=Severity.choices, default=Severity.WARNING)
    metric = models.CharField(max_length=50)  # type: ignore[var-annotated]  # 'cpu', 'memory', 'disk'
    threshold = models.FloatField()  # type: ignore[var-annotated]  # percentage
    current_value = models.FloatField()  # type: ignore[var-annotated]
    message = models.TextField()  # type: ignore[var-annotated]
    acknowledged = models.BooleanField(default=False)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]


class NotificationChannel(models.Model):
    """A delivery channel for alert notifications (email, Slack webhook, SMS, etc.)."""
    class ChannelType(models.TextChoices):
        EMAIL = 'email', 'Email'
        SLACK = 'slack', 'Slack Webhook'
        SMS = 'sms', 'SMS'
        WEBHOOK = 'webhook', 'Generic Webhook'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Friendly name for this channel")
    channel_type = models.CharField(max_length=20, choices=ChannelType.choices)
    target = models.CharField(
        max_length=500,
        help_text="Email address, Slack webhook URL, phone number, or generic webhook URL")
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.channel_type})"


class AlertRule(models.Model):
    """A platform-wide alert rule that monitors a metric condition."""
    class Severity(models.TextChoices):
        INFO = 'info', 'Info'
        WARNING = 'warning', 'Warning'
        CRITICAL = 'critical', 'Critical'

    class Metric(models.TextChoices):
        CPU = 'cpu', 'CPU Usage'
        MEMORY = 'memory', 'Memory Usage'
        DISK = 'disk', 'Disk Usage'
        STATUS = 'status', 'Service Status'
        RESPONSE_TIME = 'response_time', 'Response Time'
        ERROR_RATE = 'error_rate', 'Error Rate'

    class Operator(models.TextChoices):
        GT = '>', 'Greater than'
        GTE = '>=', 'Greater than or equal'
        LT = '<', 'Less than'
        LTE = '<=', 'Less than or equal'
        EQ = '==', 'Equal to'
        NEQ = '!=', 'Not equal to'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    enabled = models.BooleanField(default=True)
    metric = models.CharField(max_length=30, choices=Metric.choices)
    operator = models.CharField(max_length=5, choices=Operator.choices, default='>')
    threshold = models.FloatField(help_text="Threshold value for the metric")
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.WARNING)
    channels = models.ManyToManyField(NotificationChannel, blank=True, related_name='alert_rules')
    cooldown_minutes = models.PositiveIntegerField(
        default=5,
        help_text="Minimum minutes between repeated notifications for this rule")
    message_template = models.TextField(
        blank=True, default='',
        help_text="Custom alert message template. Use {metric}, {value}, {threshold}, {service} as placeholders.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.metric} {self.operator} {self.threshold})"
