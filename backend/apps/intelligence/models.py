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
    
    deepseek_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    deepseek_model = models.CharField(max_length=100, default="deepseek-coder", blank=True)

    # OpenAI-compatible Providers
    openrouter_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    openrouter_model = models.CharField(max_length=100, default="openrouter/auto", blank=True)

    groq_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    groq_model = models.CharField(max_length=100, default="llama-3.3-70b-versatile", blank=True)

    alibaba_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    alibaba_model = models.CharField(max_length=100, default="qwen-max", blank=True)

    jules_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    jules_model = models.CharField(max_length=100, default="jules-latest", blank=True)
    jules_base_url = models.CharField(
        max_length=255,
        default="https://api.jules.google.com/v1",
        blank=True,
        help_text="OpenAI-compatible base URL for Jules provider",
    )
    jules_auto_deploy_pr = models.BooleanField(
        default=True,
        help_text="Automatically redeploy services from the branch of Jules auto-fix PRs",
    )

    # Local LLM (OpenAI-compatible)
    localllm_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    localllm_model = models.CharField(max_length=100, default="local-model", blank=True)
    localllm_base_url = models.CharField(
        max_length=255,
        default="http://localhost:11434/v1",
        blank=True,
        help_text="OpenAI-compatible base URL for local LLM (e.g. Ollama, vLLM)",
    )

    # SMSLY Cloud AI
    smslycloud_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    smslycloud_model = models.CharField(max_length=100, default="smsly-latest", blank=True)

    # FreeModel.dev
    freemodel_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    freemodel_model = models.CharField(max_length=100, default="gpt-4o-mini", blank=True)
    freemodel_base_url = models.CharField(
        max_length=255,
        default="https://api.freemodel.dev/v1",
        blank=True,
        help_text="OpenAI-compatible base URL for FreeModel.dev provider",
    )

    # OpenCode API
    opencode_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    opencode_model = models.CharField(max_length=100, default="opencode-latest", blank=True)
    opencode_base_url = models.CharField(
        max_length=255,
        default="https://api.opencode.ai/v1",
        blank=True,
        help_text="OpenAI-compatible base URL for OpenCode API provider",
    )

    # Mistral AI (La Plateforme)
    mistral_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    mistral_model = models.CharField(max_length=100, default="mistral-small-latest", blank=True)
    mistral_base_url = models.CharField(
        max_length=255,
        default="https://api.mistral.ai/v1",
        blank=True,
        help_text="OpenAI-compatible base URL for Mistral provider",
    )

    # NVIDIA NIM
    nvidia_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    nvidia_model = models.CharField(max_length=100, default="nvidia/llama-3.1-nemotron-70b-instruct", blank=True)
    nvidia_base_url = models.CharField(
        max_length=255,
        default="https://integrate.api.nvidia.com/v1",
        blank=True,
        help_text="OpenAI-compatible base URL for NVIDIA NIM provider",
    )

    # Cloudflare Workers AI (via AI Gateway)
    cloudflare_api_key = EncryptedCharField(max_length=500, blank=True, null=True)
    cloudflare_model = models.CharField(max_length=100, default="@cf/meta/llama-3.1-8b-instruct", blank=True)
    cloudflare_base_url = models.CharField(
        max_length=255,
        default="https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/workers-ai",
        blank=True,
        help_text="Cloudflare AI Gateway URL. Replace YOUR_ACCOUNT_ID with your Cloudflare account ID.",
    )

    ecosystem_wave_size = models.PositiveSmallIntegerField(
        default=10,
        help_text="Maximum services deployed concurrently per dependency wave",
    )
    
    senate_enabled = models.BooleanField(
        default=True,
        help_text="Enable collaborative deliberation (Senate Committee) when 2+ providers configured",
    )
    senate_max_members = models.PositiveSmallIntegerField(
        default=5,
        help_text="Maximum number of AI members allowed in a Senate Committee",
    )
    ecosystem_wave_recheck_seconds = models.PositiveSmallIntegerField(
        default=15,
        help_text="Seconds between dependency-wave status checks",
    )

    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_solo(cls) -> "AIProviderSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
