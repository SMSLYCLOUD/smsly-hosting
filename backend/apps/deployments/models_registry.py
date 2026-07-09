import uuid

from django.conf import settings
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class RegistryCredential(models.Model):
    PROVIDER_CHOICES = [
        ('dockerhub', 'Docker Hub'),
        ('ghcr', 'GitHub Container Registry'),
        ('ecr', 'AWS ECR'),
        ('gcr', 'Google Container Registry'),
        ('acr', 'Azure Container Registry'),
        ('custom', 'Custom Registry'),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='registry_credentials')
    name = models.CharField(max_length=200)  # e.g. "My AWS ECR"
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES, default='custom')
    registry_url = models.CharField(max_length=500, help_text='e.g. ghcr.io, 123456.dkr.ecr.us-east-1.amazonaws.com')
    username = EncryptedCharField(max_length=255, blank=True, default='')
    password = EncryptedCharField(max_length=512, blank=True, default='', help_text='Token, PAT, or password')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        unique_together = ['owner', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_provider_display()})"
