from .base import AIProvider


class MockProvider(AIProvider):
    """Fallback mock provider for testing."""

    def name(self) -> str:
        return "Mock AI"

    def is_configured(self) -> bool:
        return True  # Always available as last resort

    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        raise NotImplementedError("No AI provider is configured. Add an API key in Settings > AI to use this feature.")
