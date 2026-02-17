"""Models module."""
from django.db import models
from apps.deployments.models import Service


class Domain(models.Model):
    domain_name = models.CharField(max_length=255, unique=True)
    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name='domain_instances')
    verified = models.BooleanField(default=False)
    ssl_active = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.domain_name
