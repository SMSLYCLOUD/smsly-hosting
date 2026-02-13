"""Models for AI provider configuration.

We persist AI provider settings in the DB so changes made via the UI survive
container restarts. Environment variables are still supported as a fallback.
"""

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class AIProviderSettings(models.Model):
    """Singleton table for AI provider configuration (admin-managed)."""

    class Provider(models.TextChoices):
        MOCK = "mock", "Mock"
        OPENAI = "openai", "OpenAI"
        GROK = "grok", "Grok"
        GEMINI = "gemini", "Gemini"

    # Enforce singleton row (pk=1).
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    active_provider = models.CharField(
        max_length=20,
        choices=Provider.choices,
        default=Provider.MOCK,
    )

    openai_api_key = EncryptedCharField(blank=True, null=True)
    openai_model = models.CharField(max_length=100, default="gpt-4o-mini", blank=True)

    grok_api_key = EncryptedCharField(blank=True, null=True)
    grok_model = models.CharField(max_length=100, default="grok-3-mini", blank=True)

    gemini_api_key = EncryptedCharField(blank=True, null=True)
    gemini_model = models.CharField(max_length=100, default="gemini-2.0-flash", blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls) -> "AIProviderSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

