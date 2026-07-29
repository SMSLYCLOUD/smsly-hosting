def _is_core_service(service: dict) -> bool:
    """Return True when service looks like a core/platform API."""
    name = str(service.get("name") or "").lower()
    repo = str(service.get("repo") or "").lower()
    indicators = {"core", "platform", "api", "backend", "main", "server"}
    return any(ind in name or ind in repo for ind in indicators)


def _is_auth_service(service: dict) -> bool:
    """Return True when service looks like an identity/auth provider."""
    name = str(service.get("name") or "").lower()
    indicators = {"auth", "identity", "sso", "login", "keycloak"}
    return any(ind in name for ind in indicators)


def _is_intelligence_service(service: dict) -> bool:
    """Return True when service looks like an AI/Intelligence service."""
    name = str(service.get("name") or "").lower()
    repo = str(service.get("repo") or "").lower()
    indicators = {"intelligence", "ai", "brain", "senate", "neuron", "llm", "agent"}
    return any(ind in name or ind in repo for ind in indicators)
