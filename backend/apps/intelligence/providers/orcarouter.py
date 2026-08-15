from .openai_compat import OpenAICompatibleProvider


class OrcaRouterProvider(OpenAICompatibleProvider):
    """Orca Router — OpenAI-compatible router endpoint (configure base URL)."""

    ENV_PREFIX = "ORCAROUTER"
    DEFAULT_MODEL = "orcarouter/auto"
    DEFAULT_BASE_URL = ""
    LABEL = "Orca Router"
