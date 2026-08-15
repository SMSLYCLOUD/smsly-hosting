from .openai_compat import OpenAICompatibleProvider


class AgentRouterProvider(OpenAICompatibleProvider):
    """Agent Router — OpenAI-compatible router endpoint (configure base URL)."""

    ENV_PREFIX = "AGENTROUTER"
    DEFAULT_MODEL = "agentrouter/auto"
    DEFAULT_BASE_URL = ""
    LABEL = "Agent Router"
