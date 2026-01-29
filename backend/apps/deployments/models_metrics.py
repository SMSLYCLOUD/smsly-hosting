from django.db import models
from .models import Service, TimeStampedModel

class ServiceMetric(TimeStampedModel):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='metrics')
    cpu_usage = models.DecimalField(max_digits=10, decimal_places=4, help_text="CPU cores used")
    memory_usage = models.IntegerField(help_text="Memory used in MB")
    timestamp = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-timestamp']
