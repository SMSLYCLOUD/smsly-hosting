from django.db import models
from .models import Service, TimeStampedModel

class CronJob(TimeStampedModel):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='cron_jobs')
    name = models.CharField(max_length=255)
    schedule = models.CharField(max_length=100, help_text="Cron expression (e.g., '0 * * * *')")
    command = models.CharField(max_length=512)

    def __str__(self):
        return f"{self.name} ({self.schedule})"
