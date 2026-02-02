import uuid
from django.db import models

class Volume(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Use string reference to avoid circular import
    service = models.ForeignKey('deployments.Service', on_delete=models.CASCADE, related_name='volumes')

    name = models.CharField(max_length=255)
    mount_path = models.CharField(max_length=255, help_text="Path inside container e.g. /data")
    size_gb = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.mount_path})"
