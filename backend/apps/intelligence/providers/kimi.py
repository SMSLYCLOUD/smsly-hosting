from .openai_compat import OpenAICompatibleProvider


class KimiProvider(OpenAICompatibleProvider):
    """Kimi (Moonshot AI) provider — OpenAI-compatible /chat/completions."""

    ENV_PREFIX = "KIMI"
    DEFAULT_MODEL = "kimi-latest"
    DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
    LABEL = "Kimi"
