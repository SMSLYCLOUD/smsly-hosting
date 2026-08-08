import logging
import os
import re
import subprocess

import yaml

from apps.deployments.models import PlatformConfig
from apps.deployments.utils import append_log
from apps.deployments.services.mtls_integration import (
    is_mtls_enabled,
    get_mtls_labels,
    get_mtls_env_vars,
    get_mtls_volumes,
)
from .exceptions import BuildError


logger = logging.getLogger(__name__)


class ComposeNetworkingMixin:
    # Priority names for auto-detecting the "main" service in a compose file.
    COMPOSE_MAIN_HINTS = [
        'web', 'frontend', 'backend', 'app', 'api', 'server', 'nginx',
    ]

    def _detect_compose_main_service(self, compose_path: str) -> str:
        """Parse compose YAML and pick the best 'main' service."""
        try:
            with open(compose_path, encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if not data or 'services' not in data:
                return ''
            service_names = list(data['services'].keys())
            if not service_names:
                return ''
            # Prefer known hints
            for hint in self.COMPOSE_MAIN_HINTS:
                for sn in service_names:
                    if hint in sn.lower():
                        return sn
            # Fallback: first service that has ports or build defined
            for sn in service_names:
                svc = data['services'][sn]
                if svc.get('ports') or svc.get('build'):
                    return sn
            return service_names[0]
        except Exception:  # pylint: disable=broad-exception-caught
            return ''


    def _collect_compose_domains(self) -> list:
        """Collect primary + custom domains for compose routing."""
        domains = []

        primary = (self.service.public_domain or "").strip().lower()
        if primary:
            domains.append(primary)
        else:
            default_domain = os.getenv("DEFAULT_APP_DOMAIN", "apps.example.com")
            domains.append(f"{self.service.name.lower()}.{default_domain}")

        for item in self.service.custom_domains or []:
            value = str(item or "").strip().lower()
            if value and value not in domains:
                domains.append(value)

        return domains



    def _resolve_service_network_name(self) -> str:
        """Resolve the effective Docker network name for this service's scope."""
        from apps.deployments.models.network_scope import ScopedNetwork
        project = getattr(self.service, "project", None)
        if project:
            scoped = ScopedNetwork.get_for_object(project)
            if scoped and scoped.network_name:
                return scoped.network_name
            if scoped and scoped.isolated:
                return ScopedNetwork.resolve_network_name(project)
        return os.getenv("DOCKER_NETWORK", "smsly-net")



    def _compose_traefik_labels(self, project_name: str) -> dict:
        """Build Traefik labels for compose main service at create-time."""
        is_public = bool(self.service.is_public)
        router = re.sub(r"[^a-zA-Z0-9_-]+", "-", project_name)
        domains = self._collect_compose_domains()
        host_rule = " || ".join(f"Host(`{domain}`)" for domain in domains)

        labels = {
            "managed_by": "smsly-hosting",
            "traefik.enable": "true" if is_public else "false",
            "traefik.docker.network": self._resolve_service_network_name(),
        }

        # --- mTLS: Add SPIRE workload attestation labels (all services) ---
        try:
            if is_mtls_enabled(self.service):
                mtls_labels = get_mtls_labels(self.service)
                labels.update(mtls_labels)
        except Exception as e:
            logger.debug("mTLS label injection skipped: %s", e)

        if not is_public:
            return labels

        labels[
            f"traefik.http.services.{router}.loadbalancer.server.port"
        ] = str(self.service.internal_port)

        try:
            config_obj = PlatformConfig.load()
            use_ssl = bool(config_obj.use_ssl)
            enable_crowdsec_waf = (
                bool(getattr(config_obj, 'enable_crowdsec_waf', False))
                and not bool(getattr(self.service, 'disable_crowdsec_waf', False))
            )
        except Exception:  # pylint: disable=broad-exception-caught
            use_ssl = False
            enable_crowdsec_waf = False

        enable_traefik_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if use_ssl and enable_traefik_tls:
            middlewares = f"{router}-redirect"
            if enable_crowdsec_waf:
                middlewares += ",crowdsec-bouncer"

            labels.update(
                {
                    f"traefik.http.routers.{router}-http.rule": host_rule,
                    f"traefik.http.routers.{router}-http.entrypoints": "web",
                    f"traefik.http.routers.{router}-http.middlewares": middlewares,
                    f"traefik.http.middlewares.{router}-redirect.redirectscheme.scheme": "https",
                    f"traefik.http.middlewares.{router}-redirect.redirectscheme.permanent": "true",
                    f"traefik.http.routers.{router}.rule": host_rule,
                    f"traefik.http.routers.{router}.entrypoints": "websecure",
                    f"traefik.http.routers.{router}.tls": "true",
                    f"traefik.http.routers.{router}.tls.certresolver": "letsencrypt",
                }
            )
            if enable_crowdsec_waf:
                labels[f"traefik.http.routers.{router}.middlewares"] = "crowdsec-bouncer"
            return labels

        labels.update(
            {
                f"traefik.http.routers.{router}.rule": host_rule,
                f"traefik.http.routers.{router}.entrypoints": "web",
            }
        )
        if enable_crowdsec_waf:
            labels[f"traefik.http.routers.{router}.middlewares"] = "crowdsec-bouncer"

        if use_ssl:
            middleware_name = f"{router}-forwarded-https"
            current_middlewares = labels.get(f"traefik.http.routers.{router}.middlewares", "")
            if current_middlewares:
                labels[f"traefik.http.routers.{router}.middlewares"] = f"{current_middlewares},{middleware_name}"
            else:
                labels[f"traefik.http.routers.{router}.middlewares"] = middleware_name

            labels.update(
                {
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Proto": "https",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Port": "443",
                    f"traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Ssl": "on",
                }
            )

        return labels



    def _write_compose_routing_override(self, main_service: str, project_name: str) -> str:
        """
        Write a compose override file with Traefik labels and scoped network.

        Docker labels are immutable after container creation, so labels must be
        injected into compose config before ``docker compose up``.

        All services are attached to the scoped Docker network so that every
        container can resolve addon hostnames via Docker DNS.  Addon containers
        are dual-homed to both ``smsly-net`` (infrastructure) and the scoped
        network (service resolution), so DNS resolves addon aliases on whichever
        network the query arrives from.
        """
        import os as _os

        routing_dir = self.build_dir or self.source_dir
        if not routing_dir:
            raise BuildError(
                "Cannot write compose routing override: no build/source directory available"
            )
        override_path = _os.path.join(
            routing_dir,
            f".smsly-routing-{self.deployment.id}.yml",
        )

        network_name = self._resolve_service_network_name()

        override_payload: dict = {"services": {}}

        # ── Declare the shared network as external so every service joins it ──
        override_payload["networks"] = {
            network_name: {"external": True, "name": network_name},
        }

        # Add routing labels to the main service
        override_payload["services"][main_service] = {
            "labels": self._compose_traefik_labels(project_name),
            "networks": [network_name],
        }

        # Apply security_opt + shared network to ALL services in the compose file
        compose_path = _os.path.join(routing_dir, self.service.compose_file)
        if _os.path.isfile(compose_path):
            try:
                with open(compose_path, "r", encoding="utf-8") as f:
                    user_compose = yaml.safe_load(f) or {}
                    if "services" in user_compose and isinstance(user_compose["services"], dict):
                        # SECURITY: reject privileged modes and host network
                        for svc_name, svc_config in user_compose["services"].items():
                            if isinstance(svc_config, dict):
                                if svc_config.get("privileged") is True:
                                    raise BuildError(
                                        f"Compose service '{svc_name}' has privileged: true "
                                        f"which is not allowed for security reasons."
                                    )
                                if svc_config.get("network_mode") == "host":
                                    raise BuildError(
                                        f"Compose service '{svc_name}' has network_mode: host "
                                        f"which is not allowed for security reasons."
                                    )

                        # Detect sandboxed container runtime
                        from apps.deployments.services.container_runtime import detect_best_runtime
                        compose_runtime = detect_best_runtime()

                        # Collect networks already declared in the user's compose file
                        # so we don't create duplicate entries.
                        user_networks = set(
                            (user_compose.get("networks") or {}).keys()
                        )

                        # --- mTLS: Prepare SPIRE volumes and env vars ---
                        mtls_volumes = []
                        mtls_env = {}
                        try:
                            if is_mtls_enabled(self.service):
                                for host_vol, container_path, mode in get_mtls_volumes(self.service):
                                    mtls_volumes.append(f"{host_vol}:{container_path}:{mode}")
                                mtls_env = get_mtls_env_vars(self.service)
                        except Exception as e:
                            logger.debug("mTLS compose injection skipped: %s", e)

                        for svc_name in user_compose["services"]:
                            if svc_name not in override_payload["services"]:
                                override_payload["services"][svc_name] = {}
                            override_payload["services"][svc_name]["security_opt"] = [
                                "no-new-privileges:true",
                                "apparmor:docker-default"
                            ]
                            if compose_runtime and compose_runtime != "runc":
                                override_payload["services"][svc_name]["runtime"] = compose_runtime

                            # Attach every service to the shared network so DNS
                            # resolution for addon hostnames works.  If the user's
                            # compose file already declares smsly-net for this
                            # service, the merge is a harmless no-op.
                            if network_name not in user_networks:
                                svc_networks = override_payload["services"][svc_name].get("networks") or []
                                if network_name not in svc_networks:
                                    svc_networks.append(network_name)
                                    override_payload["services"][svc_name]["networks"] = svc_networks

                            # --- mTLS: Add SPIRE volumes to every service ---
                            if mtls_volumes:
                                existing_vols = override_payload["services"][svc_name].get("volumes") or []
                                for vol in mtls_volumes:
                                    if vol not in existing_vols:
                                        existing_vols.append(vol)
                                override_payload["services"][svc_name]["volumes"] = existing_vols

                            # --- mTLS: Add SPIFFE env vars to every service ---
                            if mtls_env:
                                existing_env = override_payload["services"][svc_name].get("environment") or {}
                                if isinstance(existing_env, list):
                                    existing_env = dict(e.split("=", 1) for e in existing_env if "=" in e)
                                existing_env.update(mtls_env)
                                override_payload["services"][svc_name]["environment"] = existing_env

                        # --- mTLS: Inject Envoy sidecar if enabled ---
                        try:
                            from apps.mtls.models import MtlsConfig
                            from apps.mtls.services.envoy_sidecar import (
                                EnvoySidecar,
                                ENVOY_IMAGE,
                                SPIRE_AGENT_SOCKET_VOLUME,
                                SPIRE_SVIDS_VOLUME,
                                SPIRE_AGENT_SOCKET_CONTAINER_PATH,
                                SPIRE_SVIDS_CONTAINER_PATH,
                            )
                            mtls_config_obj = MtlsConfig.objects.filter(
                                service=self.service, enabled=True, sidecar_enabled=True
                            ).first()
                            if mtls_config_obj:
                                override_payload = EnvoySidecar.inject_sidecar_compose(
                                    self.service, override_payload
                                )
                        except Exception as e:
                            logger.debug("Envoy sidecar compose injection skipped: %s", e)
            except Exception as exc:
                # Reject the deployment entirely if compose file cannot be
                # parsed for security validation — proceeding with incomplete
                # hardening would leave services exposed.
                raise BuildError(
                    f"Failed to parse compose file for security validation: {exc}. "
                    "The compose file must be valid YAML with a 'services' key."
                ) from exc
        else:
            details = {
                "security_opt": [
                    "no-new-privileges:true",
                    "apparmor:docker-default"
                ],
                "networks": [network_name],
            }
            from apps.deployments.services.container_runtime import detect_best_runtime
            compose_runtime = detect_best_runtime()
            if compose_runtime and compose_runtime != "runc":
                details["runtime"] = compose_runtime
            override_payload["services"][main_service] = details

        with open(override_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(override_payload, handle, sort_keys=False)
        return override_path



    def _ensure_docker_network(self):
        """Ensure the Docker network for deployed services exists.

        Creates the scoped network (with configured driver, subnet, etc.)
        if it does not already exist.  Uses ``ensure_scoped_network`` to
        honour ScopedNetwork model config (internal, enable_ipv6, subnet).
        """
        from .network_scope import ensure_scoped_network, apply_egress_restrictions
        from apps.deployments.models.network_scope import ScopedNetwork

        network_name = self._resolve_service_network_name()
        try:
            project = getattr(self.service, 'project', None)
            if project:
                cfg = ScopedNetwork.resolve_network_config(project)
                ensure_scoped_network(cfg)
                egress = list(cfg.get("allowed_egress_networks", []))
                if egress:
                    apply_egress_restrictions(cfg["name"], egress)
            else:
                result = subprocess.run(
                    ['docker', 'network', 'inspect', network_name],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode != 0:
                    subprocess.run(
                        ['docker', 'network', 'create', network_name],
                        capture_output=True, text=True, timeout=10,
                    )
                    append_log(self.deployment, f"Docker network '{network_name}' created.\n")
        except Exception as e:
            append_log(self.deployment, f"Warning: could not ensure Docker network '{network_name}': {e}\n")

