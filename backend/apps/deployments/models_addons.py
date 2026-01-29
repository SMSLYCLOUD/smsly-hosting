import uuid
from django.db import models
from .models import Service, TimeStampedModel

class Addon(TimeStampedModel):
    class Type(models.TextChoices):
        POSTGRES = 'POSTGRES', 'PostgreSQL'
        REDIS = 'REDIS', 'Redis'
        MYSQL = 'MYSQL', 'MySQL'
        MONGODB = 'MONGODB', 'MongoDB'

    class Status(models.TextChoices):
        PROVISIONING = 'PROVISIONING', 'Provisioning'
        ACTIVE = 'ACTIVE', 'Active'
        FAILED = 'FAILED', 'Failed'
        DELETED = 'DELETED', 'Deleted'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='addons')
    name = models.CharField(max_length=255)
    addon_type = models.CharField(max_length=20, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROVISIONING)
    connection_url = models.CharField(max_length=512, blank=True)  # Encrypted field ideally
    
    # Coolify Integration
    coolify_uuid = models.CharField(max_length=64, blank=True, null=True,
                                    help_text="UUID of the database in Coolify")

    def __str__(self):
        return f"{self.addon_type} for {self.service.name}"

