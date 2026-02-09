"""Models module."""
import uuid
from django.db import models
from apps.deployments.models import Service


class BillingAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'auth.User',
        on_delete=models.CASCADE,
        related_name='billing_account')
    stripe_customer_id = models.CharField(
        max_length=255, blank=True, null=True)
    balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00)

    def __str__(self):
        return f"{self.user.username} ({self.stripe_customer_id})"


class UsageRecord(models.Model):
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='usage_records')
    timestamp = models.DateTimeField(auto_now_add=True)

    # Metered Values
    cpu_cores = models.DecimalField(max_digits=4, decimal_places=2)
    memory_mb = models.IntegerField()
    duration_seconds = models.IntegerField(default=3600)  # Hourly check

    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0.0000)

    def __str__(self):
        return f"{self.service.name} - {self.timestamp} - ${self.cost}"
