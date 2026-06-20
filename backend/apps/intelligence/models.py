"""Models for AI provider configuration.

We persist AI provider settings in the DB so changes made via the UI survive
container restarts. Environment variables are still supported as a fallback.

The system auto-discovers all providers with valid API keys:
- 1 key set → solo mode
- 2+ keys set → Senate Committee (collaborative deliberation)
- 0 keys set → mock fallback
"""

import ipaddress
import uuid
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

_DISALLOWED_LOCALLM_NETWORKS = (
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('fe80::/10'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('fc00::/7'),
)
_DISALLOWED_LOCALLM_HOSTS = frozenset({
    '169.254.169.254',
    'fd00:ec2::254',
    'metadata.google.internal',
    'metadata',
})


def _validate_https_allowlist(url: str, field_label: str, allowed_hosts: list[str]) -> None:
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme.lower() != 'https':
        raise ValidationError(
            {field_label: f'{field_label} must use https:// scheme; got {parsed.scheme!r}.'}
        )
    host = (parsed.hostname or '').lower()
    if not host:
        raise ValidationError({field_label: f'{field_label} must include a hostname.'})
    if host not in {h.lower() for h in allowed_hosts}:
        raise ValidationError(
            {field_label: f'{field_label} host {host!r} is not in the allowlist.'}
        )


def _validate_localllm_base_url(url: str) -> None:
    if not url:
        return
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    if not host:
        raise ValidationError(
            {'localllm_base_url': 'localllm_base_url must include a hostname.'}
        )
    if host in _DISALLOWED_LOCALLM_HOSTS:
        raise ValidationError(
            {'localllm_base_url': f'localllm_base_url host {host!r} is not allowed.'}
        )
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        for network in _DISALLOWED_LOCALLM_NETWORKS:
            if ip in network:
                raise ValidationError(
                    {'localllm_base_url': f'localllm_base_url host {host!r} is in a '
                     f'disallowed network range ({network}).'}
                )
    allowed_hosts = tuple(getattr(settings, 'LOCALLM_ALLOWED_HOSTS', ()) or ())
    allowed_hosts = {h.lower() for h in allowed_hosts}
    if not allowed_hosts or host not in allowed_hosts:
        raise ValidationError(
            {'localllm_base_url': f'localllm_base_url host {host!r} is not in the '
             f'LOCALLM_ALLOWED_HOSTS allowlist. Add it to settings.LOCALLM_ALLOWED_HOSTS '
             f'before using this provider.'}
        )


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
        default=False,
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

    def clean(self):
        super().clean()
        # SECURITY: each provider's base_url must use https and point to
        # the vendor's public host (or an allowlisted alias). This
        # blocks admins (or anyone with IsAdminUser) from pointing
        # provider requests at internal services, cloud metadata
        # endpoints, or the platform's own mesh.
        jules_hosts = list(getattr(settings, 'JULES_ALLOWED_HOSTS', ['api.jules.google.com']))
        _validate_https_allowlist(self.jules_base_url, 'jules_base_url',
                                  jules_hosts or ['api.jules.google.com'])
        # SECURITY (SSRF): the local LLM endpoint runs an OpenAI-compatible
        # call that is prefixed with the provider's API key. An admin who
        # pointed this at ``http://169.254.169.254/...`` or ``http://localhost:8000/admin/``
        # would exfiltrate that key to themselves. Reject any host that
        # resolves to loopback, link-local, RFC1918 private, IPv6 ULA, or
        # cloud-metadata ranges. Hostnames (rather than literal IPs) are
        # additionally required to be in ``settings.LOCALLM_ALLOWED_HOSTS``,
        # which defaults to an empty tuple so out-of-the-box deployments
        # cannot accidentally trust an attacker-controlled DNS name.
        _validate_localllm_base_url(self.localllm_base_url)
        _validate_https_allowlist(
            self.freemodel_base_url, 'freemodel_base_url',
            ['api.freemodel.dev'],
        )
        _validate_https_allowlist(
            self.opencode_base_url, 'opencode_base_url',
            ['api.opencode.ai'],
        )
        _validate_https_allowlist(
            self.mistral_base_url, 'mistral_base_url',
            ['api.mistral.ai'],
        )
        _validate_https_allowlist(
            self.nvidia_base_url, 'nvidia_base_url',
            ['integrate.api.nvidia.com'],
        )
        _validate_https_allowlist(
            self.cloudflare_base_url, 'cloudflare_base_url',
            ['gateway.ai.cloudflare.com'],
        )


class LLMUsage(models.Model):
    """Per-call LLM usage record for token / cost accounting."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='llm_usages',
    )
    provider = models.CharField(max_length=64)
    model = models.CharField(max_length=128, blank=True)
    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    total_tokens = models.IntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['user', '-created_at'])]


class UserAICap(models.Model):
    """Per-user daily spend / token caps for LLM calls."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='ai_cap',
    )
    daily_token_cap = models.IntegerField(default=100000)
    daily_cost_cap_usd = models.DecimalField(max_digits=8, decimal_places=2, default=10.00)
