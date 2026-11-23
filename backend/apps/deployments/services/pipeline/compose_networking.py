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
        """Collect primary + custom domains + host aliases for compose routing."""
        if getattr(self, "staged_only", False):
            staging_url = self.deployment.staging_url or self.service.generate_staging_url()
            from urllib.parse import urlparse
            staging_host = urlparse(staging_url).hostname or ""
            if not staging_host:
                raise BuildError("Could not resolve staging hostname for Compose deployment")
            if not self.deployment.staging_url:
                self.deployment.staging_url = staging_url
                self.deployment.save(update_fields=["staging_url"])
            return [staging_host]

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

        for item in self.service.host_aliases or []:
            if isinstance(item, dict):
                host = str(item.get("host") or "").strip().lower()
            else:
                host = str(item or "").strip().lower()
            if host and host not in domains:
                domains.append(host)

        return domains



    def _resolve_service_network_name(self) -> str:
        """Resolve the effective Docker network name for this service's scope.

        An explicit master-shipped project bridge (deployment metadata)
        wins — the local DB may carry no scope rows (nodes). Otherwise
        project rows, otherwise DOCKER_NETWORK/smsly-net as before.
        """
        try:
            from apps.deployments.services.network_scope import resolve_effective_network_name
            explicit = resolve_effective_network_name(
                getattr(self, "service", None), getattr(self, "deployment", None))
            if explicit:
                return explicit
        except Exception:
            pass
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
                middlewares += ",crowdsec-bouncer@docker"

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
                labels[f"traefik.http.routers.{router}.middlewares"] = "crowdsec-bouncer@docker"
            return labels

        labels.update(
            {
                f"traefik.http.routers.{router}.rule": host_rule,
                f"traefik.http.routers.{router}.entrypoints": "web",
            }
        )
        # The chain below references secure-headers-strict@file /
        # edge-forward-auth@file (file provider). Ensure edge-shared.yml
        # exists first — a missing file drops the whole router.
        try:
            from apps.deployments.services.traefik_manager.edge_shared_file import (
                ensure_edge_shared_file,
            )
            ensure_edge_shared_file()
        except Exception:
            logger.warning("Edge shared file ensure failed for %s", router)
        chain = ["secure-headers-strict@file"]
        if enable_crowdsec_waf:
            chain.insert(0, "crowdsec-bouncer@docker")
        try:
            svc_flags = getattr(self, "service", None)
            if svc_flags is not None:
                if bool(getattr(svc_flags, "sablier_enabled", False)):
                    from apps.deployments.services.internal_wake import (
                        internal_wake_host,
                        service_has_mesh_sidecar,
                    )
                    from apps.deployments.services.traefik_labels import (
                        internal_wake_labels,
                        sablier_retry_labels,
                        sablier_retry_ref,
                        sablier_service_labels,
                    )
                    if service_has_mesh_sidecar(svc_flags):
                        # Mesh-served: no in-band wake path (see internal_wake).
                        logger.warning(
                            "sablier deferred for %s: mesh sidecar attached",
                            getattr(svc_flags, "name", router),
                        )
                    else:
                        session = str(getattr(svc_flags, "sablier_session", "") or "10m").strip() or "10m"
                        group = str(getattr(svc_flags, "name", "") or router)
                        labels.update(sablier_service_labels(
                            router_name=router, group=group, session=session,
                        ))
                        labels.update(sablier_retry_labels(router_name=router))
                        labels.update(internal_wake_labels(
                            router_name=router,
                            service_ref=router,
                            internal_host=internal_wake_host(group),
                            group=group,
                            session=session,
                        ))
                        chain.append(sablier_retry_ref(router))
                        chain.append(f"{router}-sablier")
                if bool(getattr(svc_flags, "edge_jwt_required", False)):
                    chain.append("edge-forward-auth@file")
        except Exception:
            pass
        labels[f"traefik.http.routers.{router}.middlewares"] = ",".join(chain)

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



    _DANGEROUS_COMPOSE_CAPS = frozenset({
        "ALL", "SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE",
        "SYS_RAWIO", "SYS_BOOT", "MAC_ADMIN", "MAC_OVERRIDE",
        "DAC_READ_SEARCH", "BPF", "PERFMON", "SYS_TIME",
        "AUDIT_CONTROL", "SYSLOG",
    })
    _FORBIDDEN_HOST_BIND_PREFIXES = (
        "/etc", "/proc", "/sys", "/dev", "/var/run", "/run",
        "/var/lib/docker", "/var/lib/containerd", "/var/lib/smsly",
        "/root", "/boot", "/opt/smsly-hosting",
    )
    _FORBIDDEN_COMPOSE_NETWORKS = frozenset({
        "smsly-platform-net",
        "smsly-build-net",
        "traefik-proxy-net",
        "socket-proxy",
        "smsly-hosting_socket-proxy",
        "smsly-hosting_traefik-proxy-net",
        "smsly-internal-net",
        "host",
        "bridge",
    })
    _COMPOSE_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])([^}]*))?\}")

    @classmethod
    def _interpolate_compose_text(cls, raw_text: str, env_map: dict[str, str] | None = None) -> str:
        """Resolve ${VAR}, ${VAR:-default}, ${VAR-default} for pre-deploy security validation."""
        env = env_map or {}

        def _repl(match: re.Match) -> str:
            var_name = match.group(1)
            op = match.group(2) or ""
            fallback = match.group(3) or ""
            in_env = var_name in env
            val = str(env.get(var_name, "") or "")
            if op == ":-":
                return val if (in_env and val != "") else fallback
            if op == "-":
                return val if in_env else fallback
            if op in (":+", "+"):
                return fallback if (in_env and val != "") else ""
            return val

        return cls._COMPOSE_VAR_RE.sub(_repl, raw_text or "")

    @classmethod
    def _check_host_path_forbidden(cls, svc_name: str, raw_path: str, context_label: str = "volume") -> None:
        src_clean = str(raw_path or "").strip().replace("\\", "/")
        if not src_clean:
            return
        if "docker.sock" in src_clean.lower() or "containerd.sock" in src_clean.lower():
            raise BuildError(
                f"Compose service '{svc_name}' mounts docker.sock/containerd.sock "
                "which is not allowed for security reasons."
            )
        if ".." in src_clean.split("/"):
            raise BuildError(
                f"Compose service '{svc_name}' uses parent path traversal in {context_label} "
                f"'{raw_path}' which is not allowed."
            )
        if src_clean.startswith("/"):
            norm = os.path.normpath(src_clean).replace("\\", "/")
            if norm in ("/", "/var") or any(
                norm == p or norm.startswith(p + "/")
                for p in cls._FORBIDDEN_HOST_BIND_PREFIXES
            ):
                raise BuildError(
                    f"Compose service '{svc_name}' bind-mounts sensitive host path "
                    f"'{raw_path}' in {context_label} which is not allowed for security reasons."
                )

    @classmethod
    def _validate_compose_top_level_volumes(cls, compose_doc: dict) -> None:
        """Reject top-level named volumes that use driver_opts bind mounts to sensitive host paths."""
        if not isinstance(compose_doc, dict):
            return
        volumes = compose_doc.get("volumes")
        if not isinstance(volumes, dict):
            return
        for vol_name, vol_cfg in volumes.items():
            if not isinstance(vol_cfg, dict):
                continue
            if vol_cfg.get("external") is True or isinstance(vol_cfg.get("external"), dict):
                ext_name = (
                    vol_cfg["external"].get("name", vol_name)
                    if isinstance(vol_cfg.get("external"), dict)
                    else str(vol_cfg.get("name") or vol_name)
                )
                raise BuildError(
                    f"Compose volume '{vol_name}' declares external volume '{ext_name}' "
                    "which is not allowed for tenant Compose deployments."
                )
            driver_opts = vol_cfg.get("driver_opts")
            if isinstance(driver_opts, dict):
                device = str(driver_opts.get("device") or "").strip()
                if device:
                    cls._check_host_path_forbidden(f"volumes.{vol_name}", device, "driver_opts.device")

    @classmethod
    def _validate_compose_networks_security(
        cls,
        compose_doc: dict,
        allowed_network: str | None = None,
    ) -> None:
        """Reject external or infrastructure networks in top-level and service network declarations."""
        if not isinstance(compose_doc, dict):
            return
        networks = compose_doc.get("networks")
        if isinstance(networks, dict):
            for net_key, net_cfg in networks.items():
                cfg = net_cfg if isinstance(net_cfg, dict) else {}
                ext = cfg.get("external")
                resolved_name = str(cfg.get("name") or net_key).strip()
                if isinstance(ext, dict) and ext.get("name"):
                    resolved_name = str(ext.get("name")).strip()
                driver = str(cfg.get("driver") or "").strip().lower()
                if driver in ("host", "macvlan", "ipvlan"):
                    raise BuildError(
                        f"Compose network '{net_key}' uses forbidden driver '{driver}'."
                    )
                if resolved_name in cls._FORBIDDEN_COMPOSE_NETWORKS or net_key in cls._FORBIDDEN_COMPOSE_NETWORKS:
                    raise BuildError(
                        f"Compose network '{resolved_name}' is a protected platform network "
                        "and cannot be joined by tenant Compose services."
                    )
                if (
                    allowed_network
                    and allowed_network != "smsly-net"
                    and (resolved_name == "smsly-net" or net_key == "smsly-net")
                ):
                    raise BuildError(
                        "Compose file cannot attach directly to 'smsly-net' outside its scoped network."
                    )
                if bool(ext) and resolved_name != (allowed_network or ""):
                    raise BuildError(
                        f"Compose network '{net_key}' declares unauthorized external network "
                        f"'{resolved_name}'."
                    )

        services = compose_doc.get("services")
        if isinstance(services, dict):
            for svc_name, svc_cfg in services.items():
                if not isinstance(svc_cfg, dict):
                    continue
                svc_nets = svc_cfg.get("networks")
                net_names: list[str] = []
                if isinstance(svc_nets, list):
                    net_names = [str(n).strip() for n in svc_nets if n]
                elif isinstance(svc_nets, dict):
                    net_names = [str(n).strip() for n in svc_nets.keys() if n]
                for nname in net_names:
                    if nname in cls._FORBIDDEN_COMPOSE_NETWORKS:
                        raise BuildError(
                            f"Compose service '{svc_name}' joins forbidden platform network '{nname}'."
                        )
                    if allowed_network and allowed_network != "smsly-net" and nname == "smsly-net":
                        raise BuildError(
                            f"Compose service '{svc_name}' cannot join infrastructure network 'smsly-net'."
                        )

    @classmethod
    def _validate_compose_service_security(cls, svc_name: str, svc_config: dict) -> None:
        """Reject dangerous Docker Compose directives before compose build/up."""
        if not isinstance(svc_config, dict):
            return
        if svc_config.get("privileged") is True or str(svc_config.get("privileged") or "").strip().lower() == "true":
            raise BuildError(
                f"Compose service '{svc_name}' has privileged: true "
                "which is not allowed for security reasons."
            )
        net_mode = str(svc_config.get("network_mode") or "").strip().lower()
        if net_mode == "host" or net_mode.startswith("container:"):
            raise BuildError(
                f"Compose service '{svc_name}' has network_mode: {svc_config.get('network_mode')} "
                "which is not allowed for security reasons."
            )
        for ns_key in ("pid", "ipc", "uts", "userns_mode", "cgroup"):
            ns_val = str(svc_config.get(ns_key) or "").strip().lower()
            if ns_val == "host" or ns_val.startswith("container:"):
                raise BuildError(
                    f"Compose service '{svc_name}' has {ns_key}: {svc_config.get(ns_key)} "
                    "which is not allowed for security reasons."
                )
        if svc_config.get("cgroup_parent"):
            raise BuildError(
                f"Compose service '{svc_name}' sets cgroup_parent "
                "which is not allowed for security reasons."
            )
        if svc_config.get("devices"):
            raise BuildError(
                f"Compose service '{svc_name}' declares host devices "
                "which is not allowed for security reasons."
            )
        for opt in (svc_config.get("security_opt") or []):
            opt_lower = str(opt).strip().lower()
            if "unconfined" in opt_lower or "disable" in opt_lower:
                raise BuildError(
                    f"Compose service '{svc_name}' disables mandatory security profile "
                    f"({opt}) which is not allowed."
                )
        for cap in (svc_config.get("cap_add") or []):
            cap_norm = str(cap).strip().upper()
            if cap_norm.startswith("CAP_"):
                cap_norm = cap_norm[4:]
            if cap_norm in cls._DANGEROUS_COMPOSE_CAPS:
                raise BuildError(
                    f"Compose service '{svc_name}' requests forbidden capability "
                    f"'{cap}' which is not allowed for security reasons."
                )
        build_cfg = svc_config.get("build")
        if isinstance(build_cfg, str):
            cls._check_host_path_forbidden(svc_name, build_cfg, "build.context")
        elif isinstance(build_cfg, dict):
            if str(build_cfg.get("network") or "").strip().lower() == "host":
                raise BuildError(
                    f"Compose service '{svc_name}' sets build.network: host "
                    "which is not allowed for security reasons."
                )
            if build_cfg.get("privileged") is True or str(build_cfg.get("privileged") or "").strip().lower() == "true":
                raise BuildError(
                    f"Compose service '{svc_name}' sets build.privileged: true "
                    "which is not allowed for security reasons."
                )
            for bfield in ("context", "dockerfile"):
                if build_cfg.get(bfield):
                    cls._check_host_path_forbidden(svc_name, str(build_cfg[bfield]), f"build.{bfield}")
            add_ctx = build_cfg.get("additional_contexts")
            if isinstance(add_ctx, dict):
                for ctx_val in add_ctx.values():
                    cls._check_host_path_forbidden(svc_name, str(ctx_val), "build.additional_contexts")
            elif isinstance(add_ctx, list):
                for item in add_ctx:
                    _, _, ctx_val = str(item).partition("=")
                    cls._check_host_path_forbidden(svc_name, ctx_val or str(item), "build.additional_contexts")

        env_files = svc_config.get("env_file")
        if isinstance(env_files, str):
            env_files = [env_files]
        if isinstance(env_files, list):
            for ef in env_files:
                ef_path = ef.get("path", "") if isinstance(ef, dict) else str(ef or "")
                cls._check_host_path_forbidden(svc_name, ef_path, "env_file")

        for vol in (svc_config.get("volumes") or []):
            src = ""
            if isinstance(vol, str):
                parts = vol.split(":")
                if len(parts) >= 2:
                    src = parts[0].strip()
            elif isinstance(vol, dict):
                src = str(vol.get("source") or "").strip()
            if not src:
                continue
            cls._check_host_path_forbidden(svc_name, src, "volume")

    @staticmethod
    def _apply_compose_service_hardening(
        svc_override: dict,
        compose_runtime: str | None,
        sandbox_dns: list[str] | None,
        extra_hosts: dict[str, str] | list[str] | None,
        resolv_path: str | None,
        mem_limit: str = "512m",
        cpus: float = 1.0,
    ) -> None:
        """Apply LocalAdapter-equivalent hardening defaults & resource caps to a compose service override."""
        svc_override["init"] = True
        svc_override["security_opt"] = [
            "no-new-privileges:true",
            "apparmor:docker-default",
        ]
        svc_override["cap_drop"] = ["ALL"]
        svc_override["cap_add"] = ["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"]
        svc_override["pids_limit"] = 1024
        svc_override["mem_limit"] = str(mem_limit or "512m")
        svc_override["memswap_limit"] = str(mem_limit or "512m")
        svc_override["cpus"] = float(cpus if cpus and float(cpus) > 0 else 1.0)
        svc_override["ulimits"] = {
            "nofile": {"soft": 1024, "hard": 2048},
            "fsize": 512000000,
        }
        if compose_runtime and compose_runtime != "runc":
            svc_override["runtime"] = compose_runtime
        if sandbox_dns:
            svc_override["dns"] = list(sandbox_dns)
        if extra_hosts:
            if isinstance(extra_hosts, dict):
                svc_override["extra_hosts"] = [f"{k}:{v}" for k, v in extra_hosts.items()]
            else:
                svc_override["extra_hosts"] = [str(entry) for entry in extra_hosts if entry]
        if resolv_path and os.path.isfile(resolv_path):
            vols = list(svc_override.get("volumes") or [])
            bind_spec = f"{resolv_path}:/etc/resolv.conf:ro"
            if not any(":/etc/resolv.conf" in str(v) for v in vols):
                vols.append(bind_spec)
                svc_override["volumes"] = vols

    def _write_compose_routing_override(
        self,
        main_service: str,
        project_name: str,
        cryptd_env: dict[str, str] | None = None,
        host_gateway: bool = False,
    ) -> str:
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

        from apps.deployments.services.container_runtime import (
            detect_best_runtime,
            ensure_sandbox_resolv_conf,
            is_sandboxed_runtime,
            sandbox_dns_servers,
        )
        compose_runtime = detect_best_runtime()
        sandbox_dns: list[str] | None = None
        gvisor_extra_hosts: dict[str, str] | None = None
        resolv_path: str | None = None
        if compose_runtime and is_sandboxed_runtime(compose_runtime):
            try:
                sandbox_dns = sandbox_dns_servers()
            except Exception:
                sandbox_dns = ["8.8.8.8", "1.1.1.1"]
            try:
                resolv_path = ensure_sandbox_resolv_conf(sandbox_dns)
            except Exception:
                resolv_path = None
        if compose_runtime == "runsc":
            try:
                from apps.cloud.docker_client import get_docker_client
                from apps.deployments.services.container_refresh import (
                    _resolve_gvisor_extra_hosts,
                )
                gvisor_extra_hosts = _resolve_gvisor_extra_hosts(
                    getattr(self, "service", None),
                    [network_name],
                    get_docker_client(),
                )
            except Exception as exc:
                logger.debug("Compose gVisor extra_hosts resolution skipped: %s", exc)
        if host_gateway or (cryptd_env and cryptd_env.get("GRID_CRYPTD_SERVICE_ID")):
            hosts_list = list(gvisor_extra_hosts or [])
            if not any(str(entry).startswith("host.docker.internal:") for entry in hosts_list):
                hosts_list.append("host.docker.internal:host-gateway")
            gvisor_extra_hosts = hosts_list

        # Resolve effective resource limits from service quota (safe fallback 512m / 1.0 CPU).
        svc_obj = getattr(self, "service", None)
        try:
            mem_mb_val = int(getattr(svc_obj, "memory_mb", 512) or 512)
        except (TypeError, ValueError):
            mem_mb_val = 512
        mem_limit_str = f"{max(64, mem_mb_val)}m"
        try:
            cpus_val = float(getattr(svc_obj, "cpu_cores", 1.0) or 1.0)
        except (TypeError, ValueError):
            cpus_val = 1.0
        cpus_val = max(0.1, cpus_val)

        # Apply security_opt + shared network to ALL services in the compose file
        compose_path = _os.path.join(routing_dir, self.service.compose_file)
        if _os.path.isfile(compose_path):
            try:
                with open(compose_path, "r", encoding="utf-8") as f:
                    raw_compose_text = f.read()
                user_compose = yaml.safe_load(raw_compose_text) or {}
                if "services" in user_compose and isinstance(user_compose["services"], dict):
                    self._validate_compose_top_level_volumes(user_compose)
                    self._validate_compose_networks_security(user_compose, allowed_network=network_name)
                    # SECURITY: reject privileged modes, host namespaces, dangerous caps, and sensitive host mounts
                    for svc_name, svc_config in user_compose["services"].items():
                        self._validate_compose_service_security(svc_name, svc_config)

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

                    all_service_names = list(user_compose["services"].keys())
                    if main_service not in all_service_names:
                        all_service_names.append(main_service)

                    for svc_name in all_service_names:
                        if svc_name not in override_payload["services"]:
                            override_payload["services"][svc_name] = {}
                        self._apply_compose_service_hardening(
                            override_payload["services"][svc_name],
                            compose_runtime,
                            sandbox_dns,
                            gvisor_extra_hosts,
                            resolv_path,
                            mem_limit=mem_limit_str,
                            cpus=cpus_val,
                        )

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

                        # --- mTLS + grid-cryptd env vars ---
                        merged_extra_env = dict(mtls_env or {})
                        if cryptd_env:
                            merged_extra_env.update(cryptd_env)
                        if merged_extra_env:
                            existing_env = override_payload["services"][svc_name].get("environment") or {}
                            if isinstance(existing_env, list):
                                existing_env = dict(e.split("=", 1) for e in existing_env if "=" in e)
                            existing_env.update(merged_extra_env)
                            override_payload["services"][svc_name]["environment"] = existing_env

                    # --- mTLS: Inject Envoy sidecar if enabled (fail-closed) ---
                    mtls_required = False
                    mtls_config_obj = None
                    try:
                        from apps.mtls.models import MtlsConfig
                        is_eco = str(getattr(self.service, "managed_by", "") or "").upper() == "ECOSYSTEM"
                        mtls_config_obj = MtlsConfig.objects.filter(
                            service=self.service, enabled=True, sidecar_enabled=True
                        ).first()
                        mtls_required = bool(mtls_config_obj) or is_eco
                    except Exception as e:
                        logger.debug("mTLS config lookup skipped in compose override: %s", e)

                    if mtls_required:
                        try:
                            from apps.mtls.services.envoy_sidecar import EnvoySidecar
                            override_payload = EnvoySidecar.inject_sidecar_compose(
                                self.service, override_payload
                            )
                        except Exception as e:
                            raise BuildError(
                                f"Envoy sidecar compose injection failed for {getattr(self.service, 'name', main_service)}: {e}"
                            ) from e
            except BuildError:
                raise
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
                "labels": self._compose_traefik_labels(project_name),
                "networks": [network_name],
            }
            if cryptd_env:
                details["environment"] = dict(cryptd_env)
            self._apply_compose_service_hardening(
                details,
                compose_runtime,
                sandbox_dns,
                gvisor_extra_hosts,
                resolv_path,
                mem_limit=mem_limit_str,
                cpus=cpus_val,
            )
            override_payload["services"][main_service] = details

        with open(override_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(override_payload, handle, sort_keys=False)
        return override_path

    def _inject_encrypted_compose_volumes(
        self,
        compose_path: str,
        override_path: str,
        main_service: str = "",
    ) -> None:
        """Provision grid-cryptd encrypted host paths for opted-in Compose volumes post-validation."""
        svc_obj = getattr(self, "service", None)
        if svc_obj is None:
            return
        from apps.core.crypto_daemon import cryptd_client, is_service_encryption_enabled
        if not (cryptd_client.is_enabled() and is_service_encryption_enabled(svc_obj)):
            return
        if not override_path or not os.path.isfile(override_path):
            return

        require_vol = os.environ.get("CRYPTD_VOLUME_REQUIRE", "true").strip().lower() not in (
            "0", "false", "no", "off"
        )
        proj_id = str(getattr(svc_obj, "project_id", "") or "default")
        svc_id = str(getattr(svc_obj, "id", "") or "")
        svc_prefix = svc_id.replace("-", "")[:8] or "svc"

        user_services: dict = {}
        if compose_path and os.path.isfile(compose_path):
            with open(compose_path, "r", encoding="utf-8") as cf:
                user_doc = yaml.safe_load(cf) or {}
            if isinstance(user_doc.get("services"), dict):
                user_services = user_doc["services"]

        with open(override_path, "r", encoding="utf-8") as of:
            override_doc = yaml.safe_load(of) or {}
        if not isinstance(override_doc, dict):
            override_doc = {}
        override_services = override_doc.setdefault("services", {})

        def _upsert_vol_bind(svc_name: str, host_path: str, target_path: str, mode: str = "rw") -> None:
            svc_ov = override_services.setdefault(svc_name, {})
            vols = [
                v for v in (svc_ov.get("volumes") or [])
                if not (isinstance(v, str) and len(v.split(":")) >= 2 and v.split(":")[1].strip() == target_path)
            ]
            vols.append(f"{host_path}:{target_path}:{mode}")
            svc_ov["volumes"] = vols

        changed = False
        # 1. Replace user Compose named volume mounts with per-service cryptd encrypted host mounts
        for svc_name, svc_cfg in user_services.items():
            if not isinstance(svc_cfg, dict):
                continue
            for entry in (svc_cfg.get("volumes") or []):
                src, dst, mode = "", "", "rw"
                if isinstance(entry, str):
                    parts = entry.split(":")
                    if len(parts) < 2:
                        continue
                    src = parts[0].strip()
                    dst = parts[1].strip()
                    if len(parts) >= 3 and parts[2].strip():
                        mode = parts[2].strip()
                elif isinstance(entry, dict):
                    if str(entry.get("type") or "volume").strip().lower() != "volume":
                        continue
                    src = str(entry.get("source") or "").strip()
                    dst = str(entry.get("target") or "").strip()
                    mode = "ro" if entry.get("read_only") else "rw"
                if not src or not dst:
                    continue
                if src.startswith(("/", "./", "../", "~", "$")):
                    continue
                vol_key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", f"cmp-{svc_prefix}-{svc_name}-{src}").strip("-")
                try:
                    prov = cryptd_client.provision_volume(vol_key, service_id=svc_id, project_id=proj_id)
                    host_path = (prov or {}).get("host_path") or ""
                    if not host_path:
                        raise RuntimeError(f"Encrypted compose volume {vol_key} returned no host_path")
                    _upsert_vol_bind(svc_name, host_path, dst, mode)
                    changed = True
                except Exception as exc:
                    if require_vol:
                        raise BuildError(
                            f"Refusing unencrypted fallback for opted-in compose volume {src}: {exc}"
                        ) from exc
                    logger.warning("Encrypted compose volume provision skipped for %s: %s", vol_key, exc)

        # 2. Attach platform Volume model rows to main_service
        try:
            from apps.deployments.models import Volume
            target_svc = main_service or (next(iter(override_services.keys()), "") if override_services else "")
            if target_svc:
                for pvol in Volume.objects.filter(service=svc_obj):
                    try:
                        prov = cryptd_client.provision_volume(pvol.name, service_id=svc_id, project_id=proj_id)
                        host_path = (prov or {}).get("host_path") or ""
                        if not host_path:
                            raise RuntimeError(f"Encrypted volume {pvol.name} returned no host_path")
                        _upsert_vol_bind(target_svc, host_path, pvol.mount_path, "rw")
                        changed = True
                    except Exception as exc:
                        if require_vol:
                            raise BuildError(
                                f"Refusing unencrypted fallback for opted-in volume {pvol.name}: {exc}"
                            ) from exc
                        logger.warning("Encrypted volume provision skipped for %s: %s", pvol.name, exc)
        except BuildError:
            raise
        except Exception as exc:
            logger.debug("Compose platform Volume check skipped: %s", exc)

        if changed:
            with open(override_path, "w", encoding="utf-8") as of:
                yaml.safe_dump(override_doc, of, sort_keys=False)

    def _validate_merged_compose_config(
        self,
        compose_path: str,
        override_path: str | None,
        env_map: dict[str, str] | None,
        allowed_network: str | None = None,
    ) -> dict:
        """Validate the env-interpolated and override-merged Compose specification."""
        if not compose_path or not os.path.isfile(compose_path):
            raise BuildError(f"Compose file not found for merged validation: {compose_path}")
        try:
            with open(compose_path, "r", encoding="utf-8") as fh:
                raw_user = fh.read()
            interpolated = self._interpolate_compose_text(raw_user, env_map)
            user_doc = yaml.safe_load(interpolated) or {}
        except BuildError:
            raise
        except Exception as exc:
            raise BuildError(f"Failed to parse interpolated compose file: {exc}") from exc

        if not isinstance(user_doc, dict) or not isinstance(user_doc.get("services"), dict):
            raise BuildError("Interpolated compose file must contain a 'services' mapping.")

        if user_doc.get("include"):
            raise BuildError("Compose top-level 'include' is not allowed for tenant deployments.")

        self._validate_compose_top_level_volumes(user_doc)
        self._validate_compose_networks_security(user_doc, allowed_network=allowed_network)
        for svc_name, svc_cfg in user_doc["services"].items():
            if isinstance(svc_cfg, dict) and svc_cfg.get("extends"):
                ext = svc_cfg.get("extends")
                if isinstance(ext, dict) and ext.get("file"):
                    raise BuildError(
                        f"Compose service '{svc_name}' uses external file in 'extends' which is not allowed."
                    )
            self._validate_compose_service_security(svc_name, svc_cfg)

        override_doc: dict = {}
        if override_path and os.path.isfile(override_path):
            try:
                with open(override_path, "r", encoding="utf-8") as ofh:
                    override_doc = yaml.safe_load(ofh) or {}
            except Exception as exc:
                raise BuildError(f"Failed to parse compose override file: {exc}") from exc

        merged_services: dict = {}
        override_services = (override_doc.get("services") or {}) if isinstance(override_doc, dict) else {}
        for svc_name, svc_cfg in user_doc["services"].items():
            base_cfg = dict(svc_cfg) if isinstance(svc_cfg, dict) else {}
            ov_cfg = override_services.get(svc_name)
            if isinstance(ov_cfg, dict):
                base_cfg.update(ov_cfg)
                if override_path and os.path.isfile(override_path):
                    for req_key in ("cap_drop", "security_opt", "pids_limit", "mem_limit", "cpus"):
                        if req_key not in base_cfg:
                            raise BuildError(
                                f"Merged compose service '{svc_name}' is missing required hardening field '{req_key}'."
                            )
            self._validate_compose_service_security(svc_name, base_cfg)
            merged_services[svc_name] = base_cfg

        return {"services": merged_services}



    def _ensure_docker_network(self):
        """Ensure the Docker network for deployed services exists.

        Creates the scoped network (with configured driver, subnet, etc.)
        if it does not already exist.  Uses ``ensure_scoped_network`` to
        honour ScopedNetwork model config (internal, enable_ipv6, subnet).

        ISOLATION INVARIANT: services WITHOUT a project get the same
        per-service scoped bridge + egress firewall as project members —
        a bare unisolated bridge is never acceptable. Also attaches the
        edge router and this service's addons to the bridge so DNS names
        resolve for the app container.
        """
        from ..network_scope import (
            ensure_scoped_network,
            apply_egress_restrictions,
            ensure_router_on_network,
        )
        from apps.deployments.models.network_scope import ScopedNetwork

        network_name = self._resolve_service_network_name()
        try:
            project = getattr(self.service, 'project', None)
            if project:
                cfg = ScopedNetwork.resolve_network_config(project)
                ensure_scoped_network(cfg)
                egress = list(cfg.get("allowed_egress_networks", []))
                apply_egress_restrictions(cfg["name"], egress or ["0.0.0.0/0"])
            else:
                # No project (legacy path) — still isolate. Same shape as
                # spawning_service._scoped_network_for's fallback branch.
                ensure_scoped_network({
                    "name": network_name,
                    "driver": "bridge",
                    "internal": False,
                    "enable_ipv6": False,
                })
                apply_egress_restrictions(network_name, ["0.0.0.0/0"])
            ensure_router_on_network(network_name)
            try:
                # Internal wake alias: <name>.internal -> Traefik on this
                # bridge so sibling dials reach the wake router.
                # Compose env overlay is the caller's (user compose file);
                # labels still enroll the router so manual .internal URLs
                # wake. Gated on sablier enrollment; best-effort.
                if bool(getattr(self.service, "sablier_enabled", False)):
                    from apps.deployments.services.internal_wake import (
                        ensure_internal_wake_alias,
                    )
                    ensure_internal_wake_alias(
                        str(getattr(self.service, "name", "") or ""),
                        network_name,
                    )
            except Exception:
                pass
            self._attach_service_addons(network_name)
        except Exception as e:
            append_log(self.deployment, f"Warning: could not ensure Docker network '{network_name}': {e}\n")

    def _attach_service_addons(self, network_name: str) -> None:
        """Attach this service's ACTIVE addons to its scoped bridge so the
        primary container resolves addon hostnames there."""
        try:
            from apps.addons.services.addon_provisioner import AddonProvisioner
            AddonProvisioner().connect_service_addons_to_scoped_network(self.service)
        except Exception as e:
            append_log(self.deployment, f"Warning: addon network attach failed: {e}\n")
