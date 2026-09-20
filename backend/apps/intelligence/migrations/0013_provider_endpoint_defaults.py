# Provider endpoint corrections.
#
# Several shipped factory defaults pointed at dead endpoints:
# - jules_base_url ``https://api.jules.google.com/v1`` (host NXDOMAIN)
# - opencode_base_url ``https://api.opencode.ai/v1`` (all /v1/* paths 404)
# - cloudflare_base_url ``.../workers-ai`` (compat path is ``.../compat``)
# - localllm_base_url ``http://localhost:11434/v1`` (auto-activated the
#   provider on every install, stalling probed chains)
# and stale model ids (``deepseek-coder``, ``kimi-latest``,
# ``opencode-latest``) that upstream no longer serves.
#
# Schema part: move the field defaults to the corrected values (new rows).
# Data part: rewrite rows that still carry the *exact* old factory default
# to the new one. Operator-customized values are never touched. The
# localllm stock URL is intentionally NOT rewritten here — it may be a
# legitimate explicit choice (allowlisted via LOCALLM_ALLOWED_HOSTS); the
# provider's ``is_configured`` guard handles the autostart instead.

from django.db import migrations, models


OLD_NEW_PAIRS = [
    ("jules_base_url", "https://api.jules.google.com/v1", ""),
    ("opencode_base_url", "https://api.opencode.ai/v1", "https://opencode.ai/zen/v1"),
    (
        "opencode_model",
        "opencode-latest",
        "big-pickle",
    ),
    ("deepseek_model", "deepseek-coder", "deepseek-chat"),
    ("kimi_model", "kimi-latest", "kimi-k2.6"),
    (
        "cloudflare_base_url",
        "https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/workers-ai",
        "https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/compat",
    ),
]


def migrate_old_defaults_forward(apps, schema_editor):
    settings_model = apps.get_model("intelligence", "AIProviderSettings")
    for field_name, old_value, new_value in OLD_NEW_PAIRS:
        settings_model.objects.filter(**{field_name: old_value}).update(
            **{field_name: new_value}
        )


def migrate_old_defaults_backward(apps, schema_editor):
    settings_model = apps.get_model("intelligence", "AIProviderSettings")
    for field_name, old_value, new_value in OLD_NEW_PAIRS:
        settings_model.objects.filter(**{field_name: new_value}).update(
            **{field_name: old_value}
        )


class Migration(migrations.Migration):

    dependencies = [
        ('intelligence', '0012_aiprovidersettings_agentrouter_api_key_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='deepseek_model',
            field=models.CharField(blank=True, default='deepseek-chat', max_length=100),
        ),
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='jules_base_url',
            field=models.CharField(blank=True, default='', help_text='OpenAI-compatible base URL for Jules provider (operator-run Jules-compatible gateway)', max_length=255),
        ),
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='localllm_base_url',
            field=models.CharField(blank=True, default='', help_text='OpenAI-compatible base URL for local LLM (e.g. Ollama, vLLM). Empty = provider disabled.', max_length=255),
        ),
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='opencode_model',
            field=models.CharField(blank=True, default='big-pickle', max_length=100),
        ),
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='opencode_base_url',
            field=models.CharField(blank=True, default='https://opencode.ai/zen/v1', help_text='OpenAI-compatible base URL for OpenCode Zen provider', max_length=255),
        ),
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='cloudflare_base_url',
            field=models.CharField(blank=True, default='https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/compat', help_text='Cloudflare AI Gateway compat URL. Replace YOUR_ACCOUNT_ID with your Cloudflare account ID.', max_length=255),
        ),
        migrations.AlterField(
            model_name='aiprovidersettings',
            name='kimi_model',
            field=models.CharField(blank=True, default='kimi-k2.6', max_length=100),
        ),
        migrations.RunPython(
            migrate_old_defaults_forward,
            reverse_code=migrate_old_defaults_backward,
        ),
    ]
