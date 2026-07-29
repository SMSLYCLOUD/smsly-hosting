import uuid

from django.conf import settings
from django.db import models


class LLMUsage(models.Model):
    """Per-call LLM usage record for token / cost accounting."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    user = models.ForeignKey(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='llm_usages',
    )
    provider = models.CharField(max_length=64)  # type: ignore[var-annotated]
    model = models.CharField(max_length=128, blank=True)  # type: ignore[var-annotated]
    prompt_tokens = models.IntegerField(default=0)  # type: ignore[var-annotated]
    completion_tokens = models.IntegerField(default=0)  # type: ignore[var-annotated]
    total_tokens = models.IntegerField(default=0)  # type: ignore[var-annotated]
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)  # type: ignore[var-annotated]
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]

    class Meta:
        indexes = [models.Index(fields=['user', '-created_at'])]


class UserAICap(models.Model):
    """Per-user daily spend / token caps for LLM calls."""

    user = models.OneToOneField(  # type: ignore[var-annotated]
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_cap',
    )
    daily_token_cap = models.IntegerField(default=100000)  # type: ignore[var-annotated]
    daily_cost_cap_usd = models.DecimalField(max_digits=8, decimal_places=2, default=10.00)  # type: ignore[var-annotated]
