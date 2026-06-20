from django.db import models


class DailyRevenue(models.Model):
    """Pre-aggregated daily revenue snapshot."""
    date = models.DateField(unique=True)  # type: ignore[var-annotated]
    total_revenue = models.DecimalField(max_digits=12, decimal_places=2)  # type: ignore[var-annotated]
    subscription_revenue = models.DecimalField(max_digits=12, decimal_places=2)  # type: ignore[var-annotated]
    overage_revenue = models.DecimalField(max_digits=12, decimal_places=2)  # type: ignore[var-annotated]
    new_subscriptions = models.IntegerField(default=0)  # type: ignore[var-annotated]
    cancellations = models.IntegerField(default=0)  # type: ignore[var-annotated]
    active_subscribers = models.IntegerField(default=0)  # type: ignore[var-annotated]
    trial_users = models.IntegerField(default=0)  # type: ignore[var-annotated]

    def __str__(self):
        return f"Revenue {self.date}: ${self.total_revenue}"

class InfrastructureCost(models.Model):
    """Track actual infrastructure costs for margin calculation."""
    date = models.DateField()  # type: ignore[var-annotated]
    cost_type = models.CharField(choices=[  # type: ignore[var-annotated]
        ('VPS', 'VPS Hosting'), ('BANDWIDTH', 'Bandwidth'),
        ('STORAGE', 'Storage'), ('AI_API', 'AI API Costs'),
        ('DNS', 'DNS/SSL'), ('OTHER', 'Other'),
    ], max_length=20)
    amount_usd = models.DecimalField(max_digits=10, decimal_places=2)  # type: ignore[var-annotated]
    description = models.CharField(max_length=200, blank=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.date} {self.cost_type}: ${self.amount_usd}"
