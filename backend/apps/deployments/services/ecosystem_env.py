import re
import secrets
import string
from typing import Dict, Any, Tuple, List
import logging

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
    if _WEAK_LEET_RE.search(val_lower):
        return True
    return False

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
                    length = config.get("min_length", 48)
                    self.shared_secrets[group_name][var_key] = generate_strong_secret(length)

    def validate_and_resolve(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        errors = []
        if not hasattr(self.graph, 'services'):
            return True, {}, []

        self._resolve_shared_groups()

        # Build cross-service links
        service_links = {}
        for service_key, service in self.graph.services.items():
            service_links[service_key] = f"http://{service_key}"

        addon_links = {}
        for addon_key, addon in self.graph.addons.items():
            addon_type = addon.get("type", "").lower()
            if addon_type == "postgres":
                addon_links[addon_key] = f"postgresql://user:pass@{addon_key}:5432/db"
            elif addon_type == "redis":
                addon_links[addon_key] = f"redis://{addon_key}:6379/0"
            else:
                addon_links[addon_key] = f"tcp://{addon_key}:1234"

        for service_key, service in self.graph.services.items():
            service_env = {}
            for env_key, config in service.get("env", {}).items():
                source = config.get("source")
                required = config.get("required", False)
                value = None

                if source == "generated":
                    value = generate_strong_secret(config.get("min_length", 48))
                elif source == "addon":
                    addon_name = config.get('addon')
                    if addon_name in addon_links:
                        value = addon_links[addon_name]
                    else:
                        errors.append(f"Service '{service_key}' references missing addon '{addon_name}'")
                elif source == "service_public_url":
                    svc_name = config.get('service')
                    if svc_name in self.graph.services:
                        value = f"https://{svc_name}.placeholder.domain"
                    else:
                        errors.append(f"Service '{service_key}' references missing service '{svc_name}'")
                elif source == "service_internal_url":
                    svc_name = config.get('service')
                    if svc_name in service_links:
                        value = service_links[svc_name]
                    else:
                        errors.append(f"Service '{service_key}' references missing service '{svc_name}'")
                elif source == "external_required":
                    errors.append(f"Service '{service_key}' missing external required env '{env_key}'")
                elif source == "shared_group":
                    group = config.get("group")
                    # allow referencing specific variable in the group, default to env_key
                    var_name = config.get("var", env_key)
                    if group in self.shared_secrets and var_name in self.shared_secrets[group]:
                        value = self.shared_secrets[group][var_name]

                if required and not value and source != "external_required":
                    errors.append(f"Service '{service_key}' failed to resolve required env '{env_key}'")

                if value is not None:
                    if self.graph.manifest.get("mode") == "production" and is_weak_value(value) and source not in ["addon", "service_public_url", "service_internal_url"]:
                         errors.append(f"Service '{service_key}' env '{env_key}' contains weak value in production")
                    service_env[env_key] = value

            self.resolved_env[service_key] = service_env

        return len(errors) == 0, self.resolved_env, errors
