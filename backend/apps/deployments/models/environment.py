from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

from .core import TimeStampedModel
from .service import Service


class EnvironmentVariable(TimeStampedModel):
    """
    Environment variables for a service.
    """
    service = models.ForeignKey(  # type: ignore[var-annotated]
        Service,
        on_delete=models.CASCADE,
        related_name='env_vars')
    key = models.CharField(max_length=255)  # type: ignore[var-annotated]
    value = EncryptedCharField(max_length=10000, blank=True, default='')
    is_secret = models.BooleanField(default=False)  # type: ignore[var-annotated]
    is_locked = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Locked vars are never overridden by platform auto-injection during deployment")
    SOURCE_CHOICES = [
        ('USER', 'User Defined'),
        ('ADDON', 'Addon Auto-Injected'),
        ('SHORTCODE', 'Shortcode Resolved'),
        ('SYSTEM', 'System Auto-Injected'),
    ]
    source = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=SOURCE_CHOICES,
        default='USER',
        help_text="Origin of this env var")

    class Meta:
        unique_together = ('service', 'key')

    def __str__(self):
        return f"{self.key} ({self.service.name})"
