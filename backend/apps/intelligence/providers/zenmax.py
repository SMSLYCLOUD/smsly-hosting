from .openai_compat import OpenAICompatibleProvider


class ZenMaxProvider(OpenAICompatibleProvider):
    """ZenMax — OpenAI-compatible router endpoint (configure base URL)."""

    ENV_PREFIX = "ZENMAX"
    DEFAULT_MODEL = "zenmax/auto"
    DEFAULT_BASE_URL = ""
    LABEL = "ZenMax"
