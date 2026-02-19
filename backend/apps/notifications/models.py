from django.db import models
from django.conf import settings

class NotificationPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    event_type = models.CharField(choices=[
        ('deploy_success', 'Deploy Success'),
        ('deploy_failed', 'Deploy Failed'),
        ('health_alert', 'Health Alert'),
        ('billing_due', 'Billing Due'),
        ('ssl_expiring', 'SSL Expiring'),
        ('backup_completed', 'Backup Completed'),
    ], max_length=50)
    channels = models.JSONField(default=list) # ['email', 'webhook', 'in_app']

    class Meta:
        unique_together = ('user', 'event_type')

class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200)
    message = models.TextField()
    event_type = models.CharField(max_length=50)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
