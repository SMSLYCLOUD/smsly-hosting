import logging
import re
import secrets
import string
from typing import Any

logger = logging.getLogger(__name__)

# SEC-ZT-008: Weak value detection with non-alphanumeric boundaries.
# Prevents substring false positives (e.g., "testing" no longer matches "test")
# while catching delimiter-separated weak words (e.g., "my_password", "test_value").
_WEAK_PATTERNS_RE = re.compile(
    r'(?:^|[^a-zA-Z0-9])(changeme|secret|password|test|demo)(?:$|[^a-zA-Z0-9])',
    re.IGNORECASE,
)
_WEAK_LEET_RE = re.compile(
    r'(?:^|[^a-zA-Z0-9])(ch@ng3m3|s3cr3t|p@ssw0rd|t3st|d3m0)(?:$|[^a-zA-Z0-9])',
    re.IGNORECASE,
)

def generate_strong_secret(length: int = 48) -> str:
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def is_weak_value(value: str) -> bool:
    if not value:
        return True
    val_lower = str(value).lower().strip()
    if _WEAK_PATTERNS_RE.search(val_lower):
        return True
    return bool(_WEAK_LEET_RE.search(val_lower))

class EcosystemEnvResolver:
    def __init__(self, graph):
        self.graph = graph
        self.resolved_env = {}
        self.shared_secrets = {}

    def _resolve_shared_groups(self):
        for group_name, group_data in getattr(self.graph, 'shared_env', {}).items():
            self.shared_secrets[group_name] = {}
            for var_key, config in group_data.get("vars", {}).items():
                if config.get("source") == "generated":
                    length = min(config.get("min_length", 48), 1024)  # cap to prevent DoS
                    self.shared_secrets[group_name][var_key] = generate_strong_secret(length)

    def validate_and_resolve(self) -> tuple[bool, dict[str, Any], list[str]]:
        errors = []
        if not hasattr(self.graph, 'services'):
            return True, {}, []

        self._resolve_shared_groups()

        for service_key, service in self.graph.services.items():
            service_env = {}
            for env_key, config in service.get("env", {}).items():
                source = config.get("source")
                required = config.get("required", False)
                value = None

                if source == "generated":
                    value = generate_strong_secret(config.get("min_length", 48))
                elif source in ("addon", "service_public_url"):
                    # These are wired up later by the deploy flow's addon
                    # provisioning and service-linking steps.  Emitting a
                    # literal like "addon_placeholder_for_postgres" here would
                    # persist junk into the environment (reconcile only
                    # overwrites unresolved {{...}} placeholders).
                    value = None
                elif source == "external_required":
                    errors.append(f"Service '{service_key}' missing external required env '{env_key}'")
                elif source == "shared_group":
                    group = config.get("group")
                    if group in self.shared_secrets and env_key in self.shared_secrets[group]:
                        value = self.shared_secrets[group][env_key]

                if required and not value and source not in ("external_required", "addon", "service_public_url"):
                    errors.append(f"Service '{service_key}' failed to resolve required env '{env_key}'")

                if value is not None:
                    if self.graph.manifest.get("mode") == "production" and is_weak_value(value) and source not in ["addon", "service_public_url"]:
                         errors.append(f"Service '{service_key}' env '{env_key}' contains weak value in production")
                    service_env[env_key] = value

            self.resolved_env[service_key] = service_env

        return len(errors) == 0, self.resolved_env, errors
