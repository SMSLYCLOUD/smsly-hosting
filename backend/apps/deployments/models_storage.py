from django.db import models
from .models import Service, TimeStampedModel

class Volume(TimeStampedModel):
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='volumes')
    mount_path = models.CharField(max_length=255, help_text="Path in container e.g. /data")
    size_gb = models.IntegerField(default=1)

    def __str__(self):
        return f"{self.service.name} - {self.mount_path} ({self.size_gb}GB)"
