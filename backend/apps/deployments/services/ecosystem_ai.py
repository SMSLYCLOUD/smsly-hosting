import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class EcosystemDeploymentSenate:
    """Proposes environment variable resolutions for an ecosystem deployment.

    Uses the manifest-backed resolver (reading actual .env.example and
    SECRETS-MANIFEST.yaml files) rather than AI hallucination. Falls back
    to AI only when no source files are available.
    """

    @classmethod
    def propose_env_resolution(
        cls,
        graph,
        source_dirs: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Propose env resolutions for all services in the ecosystem graph.

        Args:
            graph: EcosystemGraph instance with service definitions
            source_dirs: Optional dict of service_key -> local source_dir path.
                         When provided, reads actual .env.example files for ground truth.

        Returns:
            dict with "resolutions" key containing {service_key: {env_var: value}}
        """
        from .cross_service_secret_map import build_cross_service_map, generate_secrets_for_map, get_secret_for_service
        from .manifest_env_resolver import ManifestEnvResolver

        if not hasattr(graph, "services") or not graph.services:
            return None

        # Step 1: If we have source_dirs, build cross-service secret map
        cross_service_map = None
        if source_dirs:
            try:
                secret_map = build_cross_service_map(source_dirs)
                secret_map = generate_secrets_for_map(secret_map)
                cross_service_map = secret_map
            except Exception as e:
                logger.warning("Failed to build cross-service secret map: %s", e)

        # Step 2: Resolve each service's env from its actual files
        resolutions: dict[str, dict[str, str]] = {}
        all_unresolved: list[str] = []

        for service_key in graph.services:
            env_resolution: dict[str, str] = {}

            # Get source_dir for this service
            src_dir = (source_dirs or {}).get(service_key)

            if src_dir and os.path.isdir(src_dir):
                # Use manifest-backed resolver
                resolver = ManifestEnvResolver(
                    source_dir=src_dir,
                    service_name=service_key,
                )
                resolved = resolver.resolve_all()

                # Fill in cross-service secrets from the map
                for var_name, var_value in resolved.items():
                    if not var_value and cross_service_map:
                        secret_val = get_secret_for_service(
                            cross_service_map, service_key, var_name
                        )
                        if secret_val:
                            var_value = secret_val
                    env_resolution[var_name] = var_value or ""

                if resolver.unresolved_vars:
                    all_unresolved.extend(
                        f"{service_key}.{v}" for v in resolver.unresolved_vars
                    )
            else:
                # No source dir — fall back to lightweight heuristic
                service_def = graph.services.get(service_key, {})
                env_resolution = cls._heuristic_env_for_service(
                    service_key, service_def
                )

            resolutions[service_key] = env_resolution

        # Step 3: Log unresolved vars
        if all_unresolved:
            logger.warning(
                "Unresolved required vars: %s",
                ", ".join(all_unresolved[:20]),
            )

        return {"resolutions": resolutions, "unresolved": all_unresolved}

    @classmethod
    def _heuristic_env_for_service(
        cls, service_key: str, service_def: dict[str, Any]
    ) -> dict[str, str]:
        """Lightweight heuristic when no source_dir is available."""
        env: dict[str, str] = {}
        stack = str(service_def.get("type", "")).lower()
        port = str(service_def.get("port", "8000"))

        env["PORT"] = port
        env["ENVIRONMENT"] = "production"
        env["LOG_LEVEL"] = "info"

        if "node" in stack or service_key.endswith("-web") or service_key.endswith("-frontend"):
            env["NODE_ENV"] = "production"
        else:
            env["PYTHONUNBUFFERED"] = "1"

        # Add addon placeholders
        for addon in service_def.get("addons", []):
            if addon == "POSTGRES":
                env["DATABASE_URL"] = "{{POSTGRES_URL}}"
            elif addon == "REDIS":
                env["REDIS_URL"] = "{{REDIS_URL}}"
            elif addon == "RABBITMQ":
                env["RABBITMQ_URL"] = "{{RABBITMQ_URL}}"
            elif addon == "MINIO":
                env["MINIO_ENDPOINT"] = "{{MINIO_URL}}"

        return env
