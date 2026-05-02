import logging
from typing import Dict, Any, List
from django.db import transaction
from apps.deployments.models import Service, EnvironmentVariable, Deployment
from apps.deployments.services.ecosystem_env import EcosystemEnvResolver, is_weak_value
from apps.deployments.services.ecosystem_graph import build_ecosystem_graph

logger = logging.getLogger(__name__)

def bulk_persist_and_verify_ecosystem_env(
    manifest_content: str,
    created_services: Dict[str, Service]
) -> tuple[bool, str]:
    try:
        graph = build_ecosystem_graph(manifest_content)
    except Exception as e:
        return False, f"Failed to build ecosystem graph: {e}"

    resolver = EcosystemEnvResolver(graph)
    success, resolved_envs, errors = resolver.validate_and_resolve()

    if not success:
        error_msg = "\n".join(errors)
        logger.error(f"Ecosystem environment validation failed:\n{error_msg}")
        return False, f"Environment validation failed. Missing or invalid required variables:\n{error_msg}"

    try:
        with transaction.atomic():
            for service_key, env_dict in resolved_envs.items():
                service = created_services.get(service_key)
                if not service:
                    logger.warning(f"Service {service_key} in manifest but not in created_services.")
                    continue

                for env_key, env_val in env_dict.items():
                    is_secret = any(hint in env_key.upper() for hint in ["SECRET", "KEY", "PASSWORD", "TOKEN"])
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key=env_key,
                        defaults={"value": env_val, "is_secret": is_secret}
                    )

            for service_key, env_dict in resolved_envs.items():
                service = created_services.get(service_key)
                if not service:
                    continue
                persisted_count = EnvironmentVariable.objects.filter(service=service, key__in=env_dict.keys()).count()
                if persisted_count != len(env_dict):
                    raise ValueError(f"Failed to verify persistence for service {service_key}. Expected {len(env_dict)}, found {persisted_count}")

    except Exception as e:
        logger.error(f"Failed to persist ecosystem env vars: {e}")
        return False, f"Failed to persist environment variables: {e}"

    return True, ""
