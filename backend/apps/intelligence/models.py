"""Models for AI provider configuration.

We persist AI provider settings in the DB so changes made via the UI survive
container restarts. Environment variables are still supported as a fallback.

The system auto-discovers all providers with valid API keys:
- 1 key set → solo mode
- 2+ keys set → Senate Committee (collaborative deliberation)
- 0 keys set → mock fallback
"""

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class AIProviderSettings(models.Model):
    """Singleton table for AI provider configuration (admin-managed)."""

    # Enforce singleton row (pk=1).
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    # Provider keys — set any/all. System auto-discovers configured providers.
    openai_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    openai_model = models.CharField(max_length=100, default="gpt-4o-mini", blank=True)

    grok_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    grok_model = models.CharField(max_length=100, default="grok-3-mini", blank=True)

    gemini_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    gemini_model = models.CharField(max_length=100, default="gemini-2.0-flash", blank=True)

    claude_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    claude_model = models.CharField(max_length=100, default="claude-sonnet-4-20250514", blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls) -> "AIProviderSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


