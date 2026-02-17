"""Models Addons module."""
import uuid
from encrypted_model_fields.fields import EncryptedCharField
from django.db import models
from .models import Service, TimeStampedModel


class Addon(TimeStampedModel):
    class Type(models.TextChoices):
        POSTGRES = 'POSTGRES', 'PostgreSQL'
        REDIS = 'REDIS', 'Redis'
        MYSQL = 'MYSQL', 'MySQL'
        MONGODB = 'MONGODB', 'MongoDB'
        QDRANT = 'QDRANT', 'Qdrant (Vector DB)'

    class Status(models.TextChoices):
        PROVISIONING = 'PROVISIONING', 'Provisioning'
        ACTIVE = 'ACTIVE', 'Active'
        FAILED = 'FAILED', 'Failed'
        DELETED = 'DELETED', 'Deleted'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='addons')
    name = models.CharField(max_length=255)
    addon_type = models.CharField(max_length=20, choices=Type.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING)
    connection_url = EncryptedCharField(
        max_length=512, blank=True)  # H-1 fix: encrypted at rest

    # Coolify Integration
    coolify_uuid = models.CharField(max_length=64, blank=True, null=True,
                                    help_text="UUID of the database in Coolify")

    def __str__(self):
        return f"{self.addon_type} for {self.service.name}"


class Backup(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    addon = models.ForeignKey(
        Addon,
        on_delete=models.CASCADE,
        related_name='backups')
    
    file_path = models.CharField(max_length=512, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING)
    
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"Backup {self.id} ({self.status})"
