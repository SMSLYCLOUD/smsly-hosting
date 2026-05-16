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

    # Validation Gate: Ensure a valid graph is buildable and deterministically ordered
    try:
        order = graph.get_topological_order()
    except Exception as e:
        return False, f"Validation Failed: Invalid dependency graph: {e}"

    resolver = EcosystemEnvResolver(graph)
    success, resolved_envs, errors = resolver.validate_and_resolve()

    if not success:
        error_msg = "\n".join(errors)
        logger.error(f"Ecosystem environment validation failed:\n{error_msg}")
        return False, f"Environment validation failed. Missing or invalid required variables:\n{error_msg}"

    # Explicit Validation Gate: Verify all linked services exist in creation mapping before persist
    for service_key in resolved_envs.keys():
        if service_key not in created_services:
            error_msg = f"Validation Failed: Target service '{service_key}' in plan is missing from creation map."
            logger.error(error_msg)
            return False, error_msg

    try:
        with transaction.atomic():
            for service_key, env_dict in resolved_envs.items():
                service = created_services.get(service_key)
                if not service:
                    continue

                for env_key, env_val in env_dict.items():
                    is_secret = any(hint in env_key.upper() for hint in ["SECRET", "KEY", "PASSWORD", "TOKEN"])
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key=env_key,
                        defaults={"value": env_val, "is_secret": is_secret}
                    )

            # Verification: Ensure environment mapping persists successfully
            for service_key, env_dict in resolved_envs.items():
                service = created_services.get(service_key)
                if not service:
                    continue
                persisted_count = EnvironmentVariable.objects.filter(service=service, key__in=env_dict.keys()).count()
                if persisted_count != len(env_dict):
                    raise ValueError(f"Failed to verify persistence for service {service_key}. Expected {len(env_dict)}, found {persisted_count}")

            # Store relationships (if the service model supports dependencies)
            for service_key in order:
                service = created_services.get(service_key)
                deps = graph.get_service_dependencies(service_key)
                # Ensure the service has a dependencies field or m2m relationship to store it
                # For safety, assuming no 'dependencies' field exists if this fails
                if hasattr(service, 'dependencies') and deps:
                    # Clear existing if any and add new
                    service.dependencies.clear()
                    for dep in deps:
                        if dep in created_services:
                            service.dependencies.add(created_services[dep])

    except Exception as e:
        logger.error(f"Failed to persist ecosystem env vars: {e}")
        return False, f"Failed to persist environment variables: {e}"

    return True, ""
