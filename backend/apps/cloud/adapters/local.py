"""Local module."""
import contextlib
import logging
import os
import re
import secrets
import shlex
from typing import Any

import docker
from django.conf import settings
from kubernetes import client, config

from .base import BaseCloudAdapter

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_low_resource_profile(cpu_millicores: int | None, memory_mb: int | None) -> bool:
    cpu_threshold = _env_int("LOW_RESOURCE_CPU_MILLICORES_THRESHOLD", 600, minimum=1)
    memory_threshold = _env_int("LOW_RESOURCE_MEMORY_MB_THRESHOLD", 768, minimum=64)
    cpu_low = cpu_millicores is not None and cpu_millicores > 0 and cpu_millicores <= cpu_threshold
    memory_low = memory_mb is not None and memory_mb > 0 and memory_mb <= memory_threshold
    return cpu_low or memory_low


def _coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _vpa_ceiling() -> float:
    """Hard ceiling multiplier for VPA-enabled containers (default 1.5x reservation)."""
    try:
        value = float(os.environ.get("VPA_CEILING_MULTIPLIER", "1.5"))
    except (TypeError, ValueError):
        value = 1.5
    return max(1.0, value)


def _normalize_health_path(path: str) -> str:
    value = str(path or "/").strip()
    if not value.startswith("/"):
        value = f"/{value}"
    # Strip shell metacharacters to prevent injection in health check commands
    import re as _re
    value = _re.sub(r'[^a-zA-Z0-9._/\-]', '', value)
    if not value:
        value = "/"
    return value


def _health_paths(primary_path: str | None) -> list[str]:
    values = []
    if primary_path:
        values.append(_normalize_health_path(primary_path))

    # Ordered fallback candidates for common frameworks.
    raw = os.environ.get(
        "DOCKER_HEALTHCHECK_FALLBACK_PATHS",
        "/,/health,/healthz,/ready,/live,/status",
    )
    for chunk in str(raw).split(","):
        path = _normalize_health_path(chunk.strip())
        if path and path not in values:
            values.append(path)

    if not values:
        values = ["/"]
    return values


def _build_docker_healthcheck_cmd(url_or_urls: str | list[str], timeout_seconds: int) -> str:
    """
    Build a portable in-container probe command.

    Probe order:
    1. wget
    2. curl
    3. python3/python urllib

    If none of these tools exist in the image, return success so the
    container is not marked unhealthy purely due missing probe tooling.
    """
    timeout = max(1, int(timeout_seconds or 3))
    if isinstance(url_or_urls, str):
        urls = [url_or_urls]
    else:
        urls = [str(value).strip() for value in url_or_urls if str(value).strip()]
    if not urls:
        urls = ["http://127.0.0.1/"]
    quoted_urls = " ".join(f"\"{url}\"" for url in urls)

    return (
        "if command -v wget >/dev/null 2>&1; then "
        f"for u in {quoted_urls}; do "
        "wget -q -O /dev/null \"$u\" >/dev/null 2>&1 && exit 0; "
        "done; exit 1; "
        "fi; "
        "if command -v curl >/dev/null 2>&1; then "
        f"for u in {quoted_urls}; do "
        "curl -fsS \"$u\" >/dev/null 2>&1 && exit 0; "
        "done; exit 1; "
        "fi; "
        "if command -v python3 >/dev/null 2>&1; then "
        f"for u in {quoted_urls}; do "
        "U=\"$u\" python3 -c "
        "\"import os,urllib.request;urllib.request.urlopen(os.environ['U'], timeout="
        f"{timeout}"
        ")\" >/dev/null 2>&1 && exit 0; "
        "done; exit 1; "
        "fi; "
        "if command -v python >/dev/null 2>&1; then "
        f"for u in {quoted_urls}; do "
        "U=\"$u\" python -c "
        "\"import os,urllib.request;urllib.request.urlopen(os.environ['U'], timeout="
        f"{timeout}"
        ")\" >/dev/null 2>&1 && exit 0; "
        "done; exit 1; "
        "fi; "
        "exit 0"
    )


class LocalAdapter(BaseCloudAdapter):
    """
    Adapter for Local Docker (Development) and K3s (Production).
    Auto-detects environment.
    """

    def __init__(self, mode: str = 'AUTO'):
        self.mode = mode
        self.docker_client = None
        self.batch_v1 = None

        try:
            from apps.cloud.docker_client import get_docker_client
            self.docker_client = get_docker_client()
        except Exception as e:
            logger.warning(f"Docker client not available: {e}")
            self.docker_client = None

        try:
            try:
                config.load_incluster_config()
            except BaseException:
                config.load_kube_config()
            self.k8s_client = client.CoreV1Api()
            self.k8s_apps = client.AppsV1Api()
            self.batch_v1 = client.BatchV1Api()
        except Exception as e:
            logger.warning(f"Kubernetes client not available: {e}")

    def _resolve_network_name(self) -> str:
        """Resolve the scoped Docker network name for the current service."""
        network_name = os.getenv('DOCKER_NETWORK', 'smsly-net')
        try:
            from apps.deployments.models import Service as _Svc
            # Prefer the service_id stashed by deploy_container()
            svc_id = getattr(self, '_service_id', None) or getattr(self, 'service_id', None)
            if not svc_id:
                return network_name
            _svc = (
                _Svc.objects.filter(id=svc_id)
                .select_related('project__team__organization')
                .first()
            )
            if _svc and getattr(_svc, 'project', None):
                from apps.deployments.models.network_scope import ScopedNetwork
                network_name = ScopedNetwork.resolve_network_name(_svc.project)
        except Exception as exc:
            logger.debug("Failed to resolve scoped network for service: %s", exc)
        return network_name

    def _apply_egress_restrictions(self, network_name: str) -> None:
        """Apply iptables egress restrictions to a service's scoped bridge."""
        try:
            from apps.deployments.services.network_scope import apply_egress_restrictions
            apply_egress_restrictions(network_name, ["0.0.0.0/0"])
        except Exception:
            logger.exception("Failed to apply egress restrictions for %s", network_name)

    def _get_traefik_labels(self, name: str, host_rule: str, port: str,
                            is_public: bool = True, network_name: str | None = None) -> dict[str, str]:
        """Generate consistent Traefik labels for routing."""
        if not network_name:
            network_name = self._resolve_network_name()
        router_name = name.replace('.', '-').replace('_', '-')

        enable_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        labels = {
            'traefik.enable': str(is_public).lower(),
            'traefik.docker.network': network_name,
            f'traefik.http.routers.{router_name}.rule': host_rule,
            f'traefik.http.services.{router_name}.loadbalancer.server.port': str(port),
            f'traefik.http.routers.{router_name}.priority': '100',
        }

        entrypoints = ['web']
        if enable_tls:
            entrypoints.append('websecure')
            labels[f'traefik.http.routers.{router_name}.tls.certresolver'] = 'letsencrypt'

        labels[f'traefik.http.routers.{router_name}.entrypoints'] = ','.join(entrypoints)
        return labels

    def _apply_router_special_labels(
        self,
        labels: dict[str, str],
        name: str,
        env: dict[str, str] | None,
    ) -> None:
        """Attach extra Traefik labels needed by managed routers."""
        env = env or {}
        api_base = str(env.get('AI_ROUTER_API_BASE', '') or '').strip()
        if not api_base:
            return
        if api_base in {'/v1', 'v1'}:
            return
        if not str(env.get('LITELLM_MASTER_KEY', '')).strip():
            return

        router_name = name.replace('.', '-').replace('_', '-')
        normalized_base = api_base if api_base.startswith('/') else f'/{api_base}'
        middleware_name = f'{router_name}-api-base'
        labels[f'traefik.http.middlewares.{middleware_name}.replacepathregex.regex'] = (
            f'^{re.escape(normalized_base)}/?(.*)$'
        )
        labels[f'traefik.http.middlewares.{middleware_name}.replacepathregex.replacement'] = '/v1/$1'
        labels[f'traefik.http.routers.{router_name}.middlewares'] = middleware_name
        labels[f'traefik.http.routers.{router_name}.priority'] = '1000'

    def authenticate(self) -> bool:
        return self.docker_client is not None

    def pull_image(self, image: str) -> bool:
        """Pull image from registry.

        For images tagged against a loopback registry (127.0.0.1:*, localhost:*)
        the pull is intentionally skipped: the registry is on the *host* network
        and is unreachable from inside the backend container.  Instead, we verify
        that the image is already present in the local Docker cache — it will have
        been tagged there during the build/push phase.
        """
        if not self.docker_client:
            return True

        # Detect loopback-registry images and skip the pull attempt.
        # The registry runs on the host but binds to 127.0.0.1, so a pull from
        # inside the container would always time-out/fail even when the image
        # is already present locally.
        _loopback_prefixes = ('127.0.0.1:', 'localhost:')
        if any(image.startswith(p) for p in _loopback_prefixes):
            try:
                self.docker_client.images.get(image)
                logger.info("Loopback-registry image %s found in local cache — skipping pull", image)
                return True
            except docker.errors.ImageNotFound:
                # Image not in local cache — try to retag from the unqualified local name.
                # e.g. "127.0.0.1:5000/smsly/app:abc" -> "smsly/app:abc"
                parts = image.split('/', 1)
                local_name = parts[1] if len(parts) > 1 else image
                try:
                    local_img = self.docker_client.images.get(local_name)
                    local_img.tag(image)
                    logger.info("Retagged local %s -> %s for loopback registry", local_name, image)
                    return True
                except docker.errors.ImageNotFound:
                    logger.error("Loopback-registry image %s not found in local cache", image)
                    return False
            except Exception as e:
                logger.error("Error checking local cache for %s: %s", image, e)
                return False

        try:
            logger.info("Pulling image: %s", image)
            # Authenticate via client.login() instead of the unreliable
            # auth_config parameter on pull().
            if settings.REGISTRY_USER and settings.REGISTRY_PASSWORD:
                registry_url = getattr(settings, 'CONTAINER_REGISTRY_URL', None) or ''
                try:
                    self.docker_client.login(
                        username=settings.REGISTRY_USER,
                        password=settings.REGISTRY_PASSWORD,
                        registry=registry_url,
                    )
                except Exception as login_exc:
                    logger.warning("Docker login for pull failed (%s); trying pull anyway", login_exc)
            self.docker_client.images.pull(image)
            return True
        except Exception as e:
            logger.error("Failed to pull image %s: %s", image, e)
            return False

        # pylint: disable=too-many-positional-arguments
    def deploy_container(self, service_name: str, image: str,
                         env_vars: dict[str, str], cpu: int, memory: int,
                         replicas: int = 1, vpa_enabled: bool = True, **kwargs) -> str:
        # SEC-NET-001: cache service_id so _resolve_network_name() can find
        # the service's ScopedNetwork. Previously the adapter never knew
        # which service it was deploying, so it always fell back to
        # DOCKER_NETWORK env var (or 'smsly-net'), bypassing all
        # per-project ScopedNetwork isolation.
        self._service_id = kwargs.get('service_id', '')
        volumes = kwargs.pop('volumes', None)
        healthcheck = kwargs.pop('healthcheck', None)
        restart_policy = kwargs.pop('restart_policy', 'unless-stopped')
        command = kwargs.pop('command', None)
        if self.docker_client:
            return self._deploy_docker(
                service_name, image, env_vars, volumes=volumes,
                healthcheck=healthcheck, cpu=cpu, memory=memory,
                restart_policy=restart_policy, command=command,
                vpa_enabled=vpa_enabled, **kwargs)
        else:
            raise RuntimeError("No local orchestrator available")

    # pylint: disable=too-many-positional-arguments, R0917
    def _deploy_docker(self, name: str, image: str,
                       env: dict[str, str], volumes: list[dict] | None = None,
                       project_id: str = 'default',
                       healthcheck: dict | None = None,
                       cpu: int | None = None, memory: int | None = None,
                       restart_policy: str = 'unless-stopped',
                       command=None, vpa_enabled: bool = True, **kwargs) -> str:
        """
        Blue-green Docker deployment with rollback-safe cutover.

        When a live container already exists, this method creates and validates
        a green container first, then promotes it. The old container keeps
        serving while the green candidate is warming up.
        """
        # Resolve scoped network for this service
        network_name = self._resolve_network_name()

        if not self.docker_client:
            raise RuntimeError("Docker client not available")
        try:
            self.docker_client.networks.get(network_name)
        except docker.errors.NotFound:
            driver = "bridge"
            try:
                from apps.deployments.models.network_scope import ScopedNetwork
                svc_id = getattr(self, '_service_id', None) or getattr(self, 'service_id', None)
                if svc_id:
                    from apps.deployments.models import Service as _Svc2
                    _svc2 = _Svc2.objects.filter(id=svc_id).select_related('project').first()
                    if _svc2 and getattr(_svc2, 'project', None):
                        cfg = ScopedNetwork.resolve_network_config(_svc2.project)
                        driver = cfg.get("driver", "bridge")
            except Exception as exc:
                logger.debug("Failed to resolve scoped network driver for service: %s", exc)
            self.docker_client.networks.create(network_name, driver=driver)

        # Prepare volumes
        docker_volumes = {}
        if volumes:
            for vol in volumes:
                vol_name = vol['name']
                try:
                    self.docker_client.volumes.get(vol_name)
                except docker.errors.NotFound:
                    self.docker_client.volumes.create(name=vol_name)

                docker_volumes[vol_name] = {
                    'bind': vol['mount_path'], 'mode': 'rw'}

        old_container = None
        with contextlib.suppress(docker.errors.NotFound):
            old_container = self.docker_client.containers.get(name)
        hold_for_staging = bool(str(env.get('STAGING_DOMAIN', '')).strip())
        stage_before_cutover = old_container is not None or hold_for_staging

        # Traefik routing metadata
        domain = env.get('PUBLIC_DOMAIN', f"{name}.localhost")
        port = env.get('PORT', '8000')

        is_public = True
        try:
            from apps.deployments.models import Service  # type: ignore[attr-defined]
            svc_obj = Service.objects.filter(name=name).first()
            if svc_obj is not None:
                is_public = svc_obj.is_public
        except Exception as exc:
            logger.debug("Failed to look up service %s for public flag: %s", name, exc)

        from apps.domains.utils import normalize_domain
        all_domains = [normalize_domain(domain, allow_ip=True)]
        custom = env.get('CUSTOM_DOMAINS', '')
        staging_domain = str(env.get('STAGING_DOMAIN', '') or '').strip().lower()
        if custom:
            for d in custom.split(','):
                d = d.strip()
                if d:
                    try:
                        normalized = normalize_domain(d, allow_ip=True)
                        if normalized != staging_domain:
                            all_domains.append(normalized)
                    except ValueError:
                        logger.warning("Skipping invalid custom domain: %s", d)
        # Include host aliases in the Host() rule
        aliases_raw = env.get('HOST_ALIASES', '')
        if aliases_raw:
            for d in aliases_raw.split(','):
                d = d.strip().lower()
                if d and d not in all_domains:
                    try:
                        all_domains.append(normalize_domain(d, allow_ip=True))
                    except ValueError:
                        logger.warning("Skipping invalid host alias: %s", d)
        host_rule = ' || '.join(f'Host(`{d}`)' for d in all_domains)

        try:
            from apps.deployments.models import PlatformConfig  # type: ignore[attr-defined]
            config = PlatformConfig.load()
            use_ssl = config.use_ssl
        except Exception:
            use_ssl = False

        enable_traefik_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        # Docker-native healthcheck
        low_resource_profile = _is_low_resource_profile(cpu, memory)
        min_interval = _env_int(
            "DOCKER_HEALTHCHECK_MIN_INTERVAL_SECONDS",
            20 if low_resource_profile else 12,
            minimum=1,
        )
        min_timeout = _env_int(
            "DOCKER_HEALTHCHECK_MIN_TIMEOUT_SECONDS",
            8 if low_resource_profile else 5,
            minimum=1,
        )
        min_retries = _env_int(
            "DOCKER_HEALTHCHECK_MIN_RETRIES",
            8 if low_resource_profile else 5,
            minimum=1,
        )
        start_period_seconds = _env_int(
            "DOCKER_HEALTHCHECK_START_PERIOD_SECONDS",
            180 if low_resource_profile else 120,
            minimum=1,
        )

        platform_hc_enabled = bool(healthcheck and healthcheck.get('path'))
        hc_port = (healthcheck or {}).get('port') or port
        hc_interval = min_interval
        hc_timeout = min_timeout
        hc_retries = min_retries
        hc_primary_path = "/"
        docker_healthcheck = None

        if platform_hc_enabled and healthcheck is not None:
            hc_primary_path = _normalize_health_path(healthcheck['path'])
            hc_interval = max(
                min_interval,
                _coerce_int(healthcheck.get('interval', min_interval), min_interval),
            )
            hc_timeout = max(
                min_timeout,
                _coerce_int(healthcheck.get('timeout', min_timeout), min_timeout),
            )
            hc_retries = max(
                min_retries,
                _coerce_int(healthcheck.get('retries', min_retries), min_retries),
            )
            hc_paths = _health_paths(hc_primary_path)
            hc_urls = [f"http://127.0.0.1:{hc_port}{path}" for path in hc_paths]
            hc_cmd = _build_docker_healthcheck_cmd(hc_urls, hc_timeout)
            docker_healthcheck = docker.types.Healthcheck(
                test=["CMD-SHELL", hc_cmd],
                interval=hc_interval * 1_000_000_000,
                timeout=hc_timeout * 1_000_000_000,
                retries=hc_retries,
                start_period=start_period_seconds * 1_000_000_000,
            )
        else:
            logger.info(
                "No explicit platform healthcheck for %s. Keeping image-native Docker HEALTHCHECK.",
                name,
            )

        router_name = name.replace('.', '-').replace('_', '-')
        labels = {
            'managed_by': 'smsly-hosting',
            'smsly.service_id': kwargs.get('service_id', ''),
            'smsly.blue_green.canonical_name': name,
            'smsly.blue_green.is_public': str(is_public),
            'smsly.blue_green.port': port,
            'smsly.blue_green.host_rule': host_rule,
            'smsly.blue_green.use_ssl': str(use_ssl),
            'smsly.blue_green.enable_tls': str(enable_traefik_tls),
            'smsly.blue_green.hc_path': hc_primary_path,
            'smsly.blue_green.hc_interval': str(hc_interval),
            'smsly.blue_green.hc_timeout': str(hc_timeout),
            'smsly.blue_green.restart_policy': restart_policy,
        }

        # Check if this is a staged deployment (green candidate with staging domain)
        is_staged_green = hold_for_staging

        # Traefik routing metadata
        if stage_before_cutover and not is_staged_green:
            # Green candidates should not receive traffic until promotion.
            labels['traefik.enable'] = 'false'
        elif is_staged_green:
            # Staged green: receive traffic ONLY on the staging domain
            staging_domain = env.get('STAGING_DOMAIN', '')
            staging_host_rule = f'Host(`{staging_domain}`)'
            labels.update(self._get_traefik_labels(f"{name}-staging", staging_host_rule, port, is_public, network_name=network_name))
            self._apply_router_special_labels(labels, f"{name}-staging", env)
            if platform_hc_enabled and hc_primary_path:
                staging_router = f"{name}-staging"
                # Use same fallback paths as Docker health check
                hc_paths = _health_paths(hc_primary_path)
                hc_path_primary = hc_paths[0] if hc_paths else hc_primary_path or "/"
                labels[f'traefik.http.services.{staging_router}.loadbalancer.healthcheck.path'] = hc_path_primary
                labels[f'traefik.http.services.{staging_router}.loadbalancer.healthcheck.interval'] = f"{hc_interval}s"
                labels[f'traefik.http.services.{staging_router}.loadbalancer.healthcheck.timeout'] = f"{hc_timeout}s"
            # Also store the staging domain metadata for promote-time cleanup
            labels['smsly.blue_green.staging_domain'] = staging_domain
        else:
            labels.update(self._get_traefik_labels(name, host_rule, port, is_public, network_name=network_name))
            self._apply_router_special_labels(labels, name, env)
            if platform_hc_enabled and hc_primary_path:
                # Use same fallback paths as Docker health check
                hc_paths = _health_paths(hc_primary_path)
                hc_path_primary = hc_paths[0] if hc_paths else hc_primary_path or "/"
                labels[f'traefik.http.services.{router_name}.loadbalancer.healthcheck.path'] = hc_path_primary
                labels[f'traefik.http.services.{router_name}.loadbalancer.healthcheck.interval'] = f"{hc_interval}s"
                labels[f'traefik.http.services.{router_name}.loadbalancer.healthcheck.timeout'] = f"{hc_timeout}s"

        # For preview environments on remote nodes, neutralize parent router labels
        # to prevent Traefik from routing the parent's domain to the preview container.
        # Local previews keep their Traefik labels so they route consistently with
        # remote services (Caddy → Traefik → container).
        try:
            from apps.deployments.models import Service  # type: ignore[attr-defined]
            svc_obj = Service.objects.filter(name=name).first()
            if svc_obj is not None and svc_obj.is_preview and svc_obj.parent_service:
                server = getattr(svc_obj, 'server', None)
                if server and not server.is_primary:
                    parent_name = svc_obj.parent_service.name
                    if parent_name:
                        parent_router_name = parent_name.replace('.', '-').replace('_', '-')
                        labels.update({
                            f'traefik.http.routers.{parent_router_name}.rule': 'Host(`disabled.localhost`)',
                            f'traefik.http.routers.{parent_router_name}.entrypoints': 'web',
                            f'traefik.http.routers.{parent_router_name}.priority': '0',
                            f'traefik.http.services.{parent_router_name}.loadbalancer.server.port': '0',
                        })
        except Exception as exc:
            logger.warning("Could not process preview environment routing labels for %s: %s", name, exc)

        container_name = name
        aliases = [name, f"{name}.default.internal"]

        # Add extra alias if provided (e.g. for addons)
        alias_name = kwargs.get('alias_name')
        if alias_name and alias_name != name:
            aliases.append(alias_name)

        if stage_before_cutover:
            suffix = secrets.token_hex(3)
            container_name = f"{name}-green-{suffix}"
            aliases = [container_name, f"{container_name}.default.internal"]
            if alias_name:
                aliases.append(alias_name)

        logger.info(
            "Deploy strategy for %s: %s",
            name,
            "blue-green staged cutover" if stage_before_cutover else "direct start",
        )

        run_kwargs: dict[str, Any] = {}
        ceiling = _vpa_ceiling()
        if memory and memory > 0:
            if vpa_enabled:
                run_kwargs['mem_reservation'] = f"{memory}m"
                run_kwargs['mem_limit'] = f"{int(memory * ceiling)}m"
            else:
                run_kwargs['mem_limit'] = f"{memory}m"

        if cpu and cpu > 0:
            if vpa_enabled:
                run_kwargs['cpu_shares'] = max(2, int((cpu / 1000) * 1024))
                run_kwargs['cpu_period'] = 100000
                run_kwargs['cpu_quota'] = int((cpu / 1000) * 100000 * ceiling)
            else:
                run_kwargs['cpu_period'] = 100000
                run_kwargs['cpu_quota'] = int((cpu / 1000) * 100000)

        if restart_policy == 'no':
            rp = None
        elif restart_policy == 'unless-stopped':
            rp = {"Name": "unless-stopped"}
        else:
            rp = {"Name": restart_policy, "MaximumRetryCount": 5}

        if stage_before_cutover:
            rp = {"Name": "on-failure", "MaximumRetryCount": 5}

        self._apply_egress_restrictions(network_name)

        # Dual-homing: services that opted into the internal network
        # also get attached to the platform-wide bridge ('smsly-platform-net')
        # so they can reach services in other projects without going
        # through public DNS. The two networks coexist on the same
        # container; DNS names are scoped per-network.
        networks_dict: dict[str, Any] = {
            network_name: self.docker_client.api.create_endpoint_config(
                aliases=aliases
            )
        }
        if getattr(self, '_service_id', None):
            try:
                from apps.deployments.models import Service as _SvcBridge
                _svc_obj = _SvcBridge.objects.filter(
                    id=self._service_id
                ).only('use_internal_network').first()
                if _svc_obj and getattr(_svc_obj, 'use_internal_network', True):
                    from apps.deployments.services.network_scope import ensure_platform_bridge
                    platform_bridge = ensure_platform_bridge()
                    if platform_bridge != network_name:
                        networks_dict[platform_bridge] = self.docker_client.api.create_endpoint_config(
                            aliases=aliases,
                        )
            except Exception as exc:
                logger.debug("Platform-bridge dual-homing skipped: %s", exc)

        # docker-py 7.x quirk (see _create_container_args): the high-level
        # containers.create() only honors networking_config when the
        # 'network' kwarg is ALSO passed, AND the networking_config must
        # be a PLAIN DICT (not a NetworkingConfig wrapper) so the
        # `network not in networking_config` sanity check finds the
        # primary bridge as a top-level key. Passing the wrapper object
        # makes the check fail and docker-py silently degrades to a
        # single network with no aliases — which is exactly how deploys
        # landed on the default 'bridge' (172.17.x.x) with Traefik
        # reporting "no available server". Verified live on the host:
        #   network=primary + networking_config={net1: epc, net2: epc}
        # preserves BOTH bridges and BOTH alias sets.
        create_kwargs = {
            "image": image,
            "name": container_name,
            "environment": env,
            "network": network_name,
            "networking_config": networks_dict,
            "labels": labels,
            "volumes": docker_volumes if docker_volumes else None,
            "restart_policy": rp,
            "security_opt": ["no-new-privileges:true", "apparmor:docker-default"],
            "cap_drop": ["ALL"],
            "cap_add": ["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"],
            "pids_limit": 1024,
            "read_only": True,
            "tmpfs": {"/tmp": "size=100m", "/run": "size=20m", "/app/.next/cache": "size=256m"},
            "ulimits": [
                {"Name": "nofile", "Soft": 1024, "Hard": 2048},
                {"Name": "fsize", "Soft": 512000000, "Hard": 512000000},
            ],
            **run_kwargs,
        }
        if docker_healthcheck is not None:
            create_kwargs["healthcheck"] = docker_healthcheck
        if command:
            if isinstance(command, str):
                create_kwargs["command"] = shlex.split(command)
            else:
                create_kwargs["command"] = list(command)

        from apps.deployments.services.container_runtime import get_runtime_for_container
        container_runtime = get_runtime_for_container(
            service_name=self.service_id if hasattr(self, "service_id") else "",
        )
        if container_runtime:
            create_kwargs["runtime"] = container_runtime

        # When running under gVisor (runsc), the container's userspace TCP/IP
        # stack cannot reach Docker's embedded DNS proxy at 127.0.0.11, so
        # hostname-based addon connections fail.  Work around this by
        # resolving addon hostnames to container IPs and injecting them into
        # /etc/hosts via Docker's "extra_hosts" mechanism.
        if container_runtime == "runsc":
            try:
                from apps.deployments.models.addons import Addon as _Addon
                from urllib.parse import urlparse as _urlparse
                extra_hosts = []
                # Resolve the service_id for addon lookup. Only addons the
                # service can actually reach count: its OWN addons plus
                # project-level SHARED addons (name ends with '-shared').
                # SECURITY (addon-theft): never inject other services'
                # personal addon hostnames into this container's
                # /etc/hosts — a manual service in the same project
                # would otherwise have its private DB hostname wired in.
                svc_id = getattr(self, '_service_id', None) or getattr(self, 'service_id', None)
                addon_qs = _Addon.objects.filter(status='ACTIVE')
                if svc_id:
                    from apps.deployments.models import Service as _AddonSvc
                    from django.db.models import Q
                    _addsvc = _AddonSvc.objects.filter(id=svc_id).select_related('project').first()
                    if _addsvc and _addsvc.project:
                        addon_qs = addon_qs.filter(
                            Q(service=_addsvc)
                            | Q(service__project=_addsvc.project, name__endswith='-shared'),
                        )
                for _addon in addon_qs:
                    if not _addon.connection_url:
                        continue
                    _parsed = _urlparse(_addon.connection_url)
                    _host = _parsed.hostname or ''
                    _addon_container = f"smsly-addon-{_addon.addon_type.lower()}-{_addon.id}"
                    try:
                        _container = self.docker_client.containers.get(_addon_container)
                        _container.reload()
                        _net_info = (_container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
                        _net_data = _net_info.get(network_name) or {}
                        _ip = _net_data.get('IPAddress', '')
                        if _ip and _host and _host != _ip:
                            extra_hosts.append(f"{_host}:{_ip}")
                    except Exception:
                        pass
                if extra_hosts:
                    create_kwargs["extra_hosts"] = extra_hosts
                    logger.info(
                        "gVisor detected: injecting %d addon hostname->IP mappings into /etc/hosts: %s",
                        len(extra_hosts), extra_hosts,
                    )
            except Exception as exc:
                logger.warning("Failed to resolve addon IPs for gVisor extra_hosts: %s", exc)

        new_container = self.docker_client.containers.create(**create_kwargs)
        new_container.start()
        logger.info(
            "Started container %s (canonical=%s, staged=%s)",
            container_name,
            name,
            stage_before_cutover,
        )

        health_timeout_default = 600 if low_resource_profile else 480
        health_timeout = _env_int(
            'BLUE_GREEN_HEALTH_TIMEOUT',
            health_timeout_default,
            minimum=30,
        )
        health_timeout = max(health_timeout, start_period_seconds + 120)
        new_healthy = self._wait_container_healthy(
            new_container.id, timeout_seconds=health_timeout
        )

        if not new_healthy:
            container_logs = ""
            status = "unknown"
            health_state = "n/a"
            exit_code = "unknown"
            oom_killed = False
            log_tail_lines = _env_int("FAILED_CONTAINER_LOG_TAIL_LINES", 200, minimum=20)
            try:
                new_container.reload()
                state = new_container.attrs.get("State", {})
                status = state.get("Status", "unknown")
                exit_code = state.get("ExitCode", "unknown")
                oom_killed = state.get("OOMKilled", False)
                health_state = (state.get("Health") or {}).get("Status", "n/a")
                log_bytes = new_container.logs(tail=log_tail_lines)
                container_logs = log_bytes.decode("utf-8", errors="replace")
                logger.error(
                    "Container %s failed health check. status=%s health=%s "
                    "exit_code=%s oom_killed=%s\nLogs:\n%s",
                    container_name, status, health_state, exit_code, oom_killed,
                    container_logs,
                )
            except Exception as log_exc:
                logger.error(
                    "Container %s failed health check (could not capture logs: %s)",
                    container_name, log_exc,
                )

            keep_failed_container = _env_bool(
                "KEEP_FAILED_CONTAINER_ON_HEALTHCHECK_ERROR",
                default=True,
            )
            if not keep_failed_container:
                with contextlib.suppress(Exception):
                    new_container.remove(force=True)
            else:
                logger.warning(
                    "Preserving failed container %s for debugging (status=%s health=%s).",
                    container_name,
                    status,
                    health_state,
                )

            detail = (
                f"Container {container_name} failed health check after deploy "
                f"(status={status}, health={health_state}, "
                f"exit_code={exit_code}, oom_killed={oom_killed})."
            )
            if container_logs:
                snippet_lines = _env_int(
                    "FAILED_CONTAINER_ERROR_SNIPPET_LINES",
                    40,
                    minimum=5,
                )
                snippet = "\n".join(container_logs.strip().splitlines()[-snippet_lines:])
                detail += f"\n--- Last logs ---\n{snippet}"
            if keep_failed_container:
                detail += (
                    "\nContainer was preserved for debugging. "
                    "Run docker logs/inspect on this container."
                )
            raise RuntimeError(detail)

        if stage_before_cutover and not hold_for_staging:
            logger.info(
                "Green container %s is healthy; promoting as %s",
                container_name,
                name,
            )
            return self.promote_container(name, new_container.id)

        if hold_for_staging:
            # Auto-promote after a configurable hold period. Push/webhook
            # deploys run unattended — waiting forever for a human would
            # mean the green sits as a ghost container indefinitely, and
            # any later cleanup pass would tear it down.
            #
            # Settings (in priority order):
            # 1. PlatformConfig.blue_green_auto_promote / .staging_hold_seconds
            #    (editable from /console/settings — the operator's
            #    preferred path)
            # 2. Env vars BLUE_GREEN_AUTO_PROMOTE / BLUE_GREEN_STAGING_HOLD_SECONDS
            #    (fallback for hosts that haven't saved PlatformConfig yet)
            # 3. Hardcoded defaults (60s hold, auto-promote on ecosystem only)
            try:
                from apps.deployments.models.core import PlatformConfig
                _pc = PlatformConfig.load()
                hold_seconds = int(_pc.blue_green_staging_hold_seconds or 60)
                auto_promote_flag = bool(_pc.blue_green_auto_promote)
            except Exception:
                hold_seconds = _env_int('BLUE_GREEN_STAGING_HOLD_SECONDS', 60, minimum=0)
                auto_promote_flag = _env_bool('BLUE_GREEN_AUTO_PROMOTE', default=False)

            commit_hash = ''
            # Resolve the Service object from the stashed service_id so we
            # can look up the latest Deployment's commit_hash for the
            # ecosystem-deploy auto-promote heuristic. Previously this
            # referenced self._service which was never set, so the
            # ecosystem-deploy check always saw an empty commit_hash.
            svc_id = getattr(self, '_service_id', None) or getattr(self, 'service_id', None)
            svc_obj = None
            if svc_id:
                from apps.deployments.models import Service as _Svc
                try:
                    svc_obj = _Svc.objects.get(id=svc_id)
                except Exception:
                    svc_obj = None
            if svc_obj is not None:
                from apps.deployments.models.deployment import Deployment
                d = Deployment.objects.filter(service=svc_obj).order_by('-created_at').first()
                if d is not None:
                    commit_hash = str(d.commit_hash or '').strip()
            auto_promote = (
                hold_seconds > 0
                and (
                    auto_promote_flag
                    or commit_hash == 'ecosystem-deploy'
                )
            )
            if auto_promote:
                logger.info(
                    "Staged green %s is healthy; auto-promoting after %ds "
                    "hold (commit_hash=%s, platform_auto_promote=%s)",
                    container_name, hold_seconds, commit_hash or '(none)',
                    auto_promote_flag,
                )
                # Sleep the hold period before promoting. If the green goes
                # unhealthy in the meantime, _promote will fail and roll
                # back via the existing safety net.
                import time as _time
                _time.sleep(hold_seconds)
                return self.promote_container(name, new_container.id)

            logger.info(
                "Green container %s is healthy and held for staging review "
                "(PlatformConfig.blue_green_auto_promote or set "
                "BLUE_GREEN_AUTO_PROMOTE=1 to auto-promote on hold expiry)",
                container_name,
            )
            # Cache the platform-bridge IP for the API surface — the
            # service detail page surfaces this so other services know
            # how to dial it cross-project.
            try:
                _new.reload()
                _pnets = _new.attrs.get('NetworkSettings', {}).get('Networks', {}) or {}
                for _pn, _pd in _pnets.items():
                    if _pn == 'smsly-platform-net':
                        _svc_db = Service.objects.filter(id=getattr(self, '_service_id', None)).first()
                        if _svc_db:
                            _svc_db.platform_internal_ip = _pd.get('IPAddress') or None
                            _svc_db.save(update_fields=['platform_internal_ip', 'updated_at'])
                        break
            except Exception as exc:
                logger.debug("Could not cache platform IP for %s: %s", container_name, exc)
            return new_container.id

        logger.info("Container %s is healthy and serving traffic", name)
        return new_container.id

    def promote_container(self, name: str, green_container_id: str) -> str:
        """
        Promote a staged green container to live with rollback safety.

        Sequence:
        1. Preserve current live container by renaming it to a backup name.
        2. Recreate green as canonical container with live routing labels.
        3. Verify promoted container health.
        4. Remove old backup only after successful cutover.
        If any step fails, restore the previous live container name.
        """
        network_name = self._resolve_network_name()

        try:
            if self.docker_client is None:
                raise RuntimeError("Docker client unavailable")
            green = self.docker_client.containers.get(green_container_id)
        except docker.errors.NotFound:
            raise RuntimeError(
                f"Green container {green_container_id} not found - "
                f"may have crashed during bake period"
            )

        green.reload()
        green_state = green.attrs.get('State', {}) or {}
        state = (green_state.get('Status') or '').lower()
        health = (green_state.get('Health', {}).get('Status') or '').lower()
        if state != 'running':
            raise RuntimeError(
                f"Green container not running (status={state}, health={health})"
            )
        if health == 'unhealthy':
            raise RuntimeError("Green container is unhealthy - aborting promotion")

        green_labels = green.labels or {}
        if (
            green.name == name
            and str(green_labels.get('traefik.enable', '')).strip().lower() == 'true'
        ):
            return green.id

        is_public = green_labels.get('smsly.blue_green.is_public', 'True') == 'True'
        port = green_labels.get('smsly.blue_green.port', '8000')
        host_rule = green_labels.get('smsly.blue_green.host_rule', f"Host(`{name}.localhost`)")
        green_config = green.attrs.get('Config', {}) or {}
        green_host_config = green.attrs.get('HostConfig', {}) or {}
        green_env = green_config.get('Env', [])

        live_labels = {
            'managed_by': 'smsly-hosting',
        }
        # Add core routing labels
        live_labels.update(self._get_traefik_labels(name, host_rule, port, is_public, network_name=network_name))
        promoted_env = {}
        for item in green_env:
            key, _sep, value = str(item).partition("=")
            if key:
                promoted_env[key] = value
        self._apply_router_special_labels(live_labels, name, promoted_env)

        # Preserve metadata labels
        for k, v in green_labels.items():
            if k.startswith('smsly.'):
                live_labels[k] = v

        # For preview environments on remote nodes, neutralize parent router labels
        # to prevent Traefik from routing the parent's domain to the preview container.
        # Local previews keep their Traefik labels so they route consistently with
        # remote services (Caddy → Traefik → container).
        try:
            from apps.deployments.models import Service  # type: ignore[attr-defined]
            svc_obj = Service.objects.filter(name=name).first()
            if svc_obj is not None and svc_obj.is_preview and svc_obj.parent_service:
                server = getattr(svc_obj, 'server', None)
                if server and not server.is_primary:
                    parent_name = svc_obj.parent_service.name
                    if parent_name:
                        parent_router_name = parent_name.replace('.', '-').replace('_', '-')
                        live_labels.update({
                            f'traefik.http.routers.{parent_router_name}.rule': 'Host(`disabled.localhost`)',
                            f'traefik.http.routers.{parent_router_name}.entrypoints': 'web',
                            f'traefik.http.routers.{parent_router_name}.priority': '0',
                            f'traefik.http.services.{parent_router_name}.loadbalancer.server.port': '0',
                        })
        except Exception as exc:
            logger.warning("Could not process preview environment routing labels for %s: %s", name, exc)

        # Add Traefik load balancer healthcheck if configured
        hc_path = green_labels.get('smsly.blue_green.hc_path')
        hc_interval = green_labels.get('smsly.blue_green.hc_interval')
        hc_timeout = green_labels.get('smsly.blue_green.hc_timeout')
        if hc_path:
            router_name = name.replace('.', '-').replace('_', '-')
            # Use same fallback paths as Docker health check
            hc_paths = _health_paths(hc_path)
            hc_path_primary = hc_paths[0] if hc_paths else hc_path or "/"
            live_labels[f'traefik.http.services.{router_name}.loadbalancer.healthcheck.path'] = hc_path_primary
            if hc_interval:
                live_labels[f'traefik.http.services.{router_name}.loadbalancer.healthcheck.interval'] = f"{hc_interval}s"
            if hc_timeout:
                live_labels[f'traefik.http.services.{router_name}.loadbalancer.healthcheck.timeout'] = f"{hc_timeout}s"

        green_cmd = green_config.get('Cmd')
        green_entrypoint = green_config.get('Entrypoint')
        green_healthcheck = green_config.get('Healthcheck')
        green_volumes = green_host_config.get('Binds')

        image_ref = ""
        try:
            image_ref = (green.image.tags or [])[0]
        except Exception:
            image_ref = ""
        if not image_ref:
            image_ref = green.image.id

        run_kwargs: dict[str, Any] = {}
        mem_limit = green_host_config.get('Memory')
        if mem_limit and mem_limit > 0:
            run_kwargs['mem_limit'] = mem_limit
        mem_reservation = green_host_config.get('MemoryReservation')
        if mem_reservation and mem_reservation > 0:
            run_kwargs['mem_reservation'] = mem_reservation

        cpu_period = green_host_config.get('CpuPeriod')
        cpu_quota = green_host_config.get('CpuQuota')
        if cpu_period and cpu_quota:
            run_kwargs['cpu_period'] = cpu_period
            run_kwargs['cpu_quota'] = cpu_quota
        cpu_shares = green_host_config.get('CpuShares')
        if cpu_shares and cpu_shares > 0:
            run_kwargs['cpu_shares'] = cpu_shares

        # Resolve restart policy for the promoted container.
        # Green candidates use bounded on-failure during warm-up; the
        # promoted container uses the service's configured policy,
        # preserved via a label on the green container.
        promoted_restart_policy = green_labels.get('smsly.blue_green.restart_policy', 'unless-stopped')
        if promoted_restart_policy == 'no':
            rp = None
        elif promoted_restart_policy == 'unless-stopped':
            rp = {"Name": "unless-stopped"}
        else:
            rp = {"Name": promoted_restart_policy, "MaximumRetryCount": 5}

        old_container = None
        backup_name = ""
        try:
            if self.docker_client is None:
                raise RuntimeError("Docker client unavailable")
            old_container = self.docker_client.containers.get(name)
            backup_name = f"{name}-rollback-{secrets.token_hex(3)}"
            # Stop the old container BEFORE renaming/creating the new one
            # to avoid a Traefik router conflict: both containers would
            # share identical routing labels (Docker labels are immutable on
            # running containers).  The rollback logic below restarts the
            # backup if promote fails.
            try:
                old_container.stop(timeout=10)
            except Exception:
                pass
            old_container.rename(backup_name)
            # Labels are immutable on running containers — Docker SDK update()
            # does not support them. Store TTL as an env var instead so the
            # stale scanner can read it from inspect.
            try:
                grace_min = 10
                from apps.deployments.models.platform import PlatformConfig
                grace_min = PlatformConfig.load().rollback_grace_minutes or 10
            except Exception:
                grace_min = 10
            import time as _time
            ttl_epoch = str(_time.time() + grace_min * 60)
            try:
                old_container.update(labels={'smsly.rollback.ttl': ttl_epoch})
            except (TypeError, ValueError):
                # Docker SDK update() does not support labels — non-fatal.
                pass
            logger.info(
                "Blue-green promote: preserved live container as %s (TTL %s min)",
                backup_name,
                grace_min,
            )
        except docker.errors.NotFound:
            logger.info("Blue-green promote: no existing live container for %s", name)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to preserve current live container before promote: {exc}"
            ) from exc

        promoted = None
        promote_health_timeout = _env_int(
            "BLUE_GREEN_PROMOTE_HEALTH_TIMEOUT_SECONDS",
            300,
            minimum=30,
        )
        green_start_period = _env_int(
            "DOCKER_HEALTHCHECK_START_PERIOD_SECONDS",
            120,
            minimum=1,
        )
        promote_health_timeout = max(promote_health_timeout, green_start_period + 120)
        try:
            if self.docker_client is None:
                raise RuntimeError("Docker client unavailable")

            self._apply_egress_restrictions(network_name)

            # Same dual-homing logic as _deploy_docker. The promoted
            # container must be on BOTH the project-scoped bridge and the
            # platform-wide bridge so service-to-service traffic (intra- AND
            # inter-project) stays host-internal.
            from apps.deployments.services.network_scope import ensure_platform_bridge
            promote_aliases = [name, f"{name}.default.internal"]
            promote_networks_dict: dict[str, Any] = {
                network_name: self.docker_client.api.create_endpoint_config(
                    aliases=promote_aliases
                )
            }
            try:
                _promote_svc = Service.objects.filter(
                    id=getattr(self, '_service_id', None)
                ).only('use_internal_network').first() if getattr(self, '_service_id', None) else None
                if _promote_svc and getattr(_promote_svc, 'use_internal_network', True):
                    platform_bridge = ensure_platform_bridge()
                    if platform_bridge != network_name:
                        promote_networks_dict[platform_bridge] = (
                            self.docker_client.api.create_endpoint_config(
                                aliases=promote_aliases
                            )
                        )
            except Exception as exc:
                logger.debug("Promote dual-homing skipped: %s", exc)

            create_kwargs = {
                "image": image_ref,
                "name": name,
                "environment": green_env,
                # docker-py 7.x quirk (see _deploy_docker for the full
                # story): networking_config alone is silently dropped by
                # the high-level create(); it must be a PLAIN DICT passed
                # alongside 'network' (the primary bridge) or the promoted
                # container lands on the default bridge and Traefik
                # reports "no available server".
                "network": network_name,
                "networking_config": promote_networks_dict,
                "labels": live_labels,
                "volumes": green_volumes,
                "restart_policy": rp,
                "command": green_cmd,
                "entrypoint": green_entrypoint,
                "security_opt": ["no-new-privileges:true", "apparmor:docker-default"],
                "cap_drop": ["ALL"],
                "cap_add": ["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"],
                "pids_limit": 1024,
                **run_kwargs,
            }
            if green_healthcheck is not None:
                create_kwargs["healthcheck"] = green_healthcheck

            promoted = self.docker_client.containers.create(**create_kwargs)
            promoted.start()

            if not self._wait_container_healthy(
                promoted.id, timeout_seconds=promote_health_timeout
            ):
                raise RuntimeError(
                    f"Promoted container failed health checks: {promoted.id[:12]}"
                )

            # Self-heal the network attachments. The create() call above
            # SHOULD have attached both bridges (plain-dict networking_config),
            # but if the Docker daemon raced us or the endpoint config was
            # rejected, the container may be missing a bridge. Verify actual
            # attachment and repair it instead of blindly disconnecting
            # (the old disconnect-then-connect pattern threw
            # "container is not connected to network" and left the promoted
            # container unreachable by Traefik).
            try:
                promoted.reload()
                attached = set(
                    ((promoted.attrs.get('NetworkSettings') or {}).get('Networks') or {}).keys()
                )
                expected = {network_name}
                try:
                    _bridge_check = ensure_platform_bridge()
                    expected.add(_bridge_check)
                except Exception:
                    pass
                for needed_net in sorted(expected):
                    if needed_net in attached:
                        continue
                    try:
                        missing_net = self.docker_client.networks.get(needed_net)
                        missing_net.connect(promoted, aliases=[name, f"{name}.default.internal"])
                        logger.info(
                            "Blue-green promote: repaired missing network attachment %s for %s",
                            needed_net, name,
                        )
                    except Exception as repair_exc:
                        logger.warning(
                            "Blue-green promote: could not attach %s to %s: %s",
                            name, needed_net, repair_exc,
                        )
            except Exception as exc:
                logger.warning(
                    "Blue-green promote: network attachment check failed: %s",
                    exc,
                )

            # Cache the platform-bridge IP for the API surface. The
            # promote path is the canonical case (skip_review=True
            # services auto-promote here) so this is the most common
            # code path; without it the frontend would show null for
            # platform_internal_ip on every actively-running service.
            try:
                promoted.reload()
                for _pn, _pd in (promoted.attrs.get('NetworkSettings', {}) or {}).get('Networks', {}).items():
                    if _pn == 'smsly-platform-net':
                        _psvc = Service.objects.filter(
                            id=getattr(self, '_service_id', None)
                        ).first() if getattr(self, '_service_id', None) else None
                        if _psvc:
                            _psvc.platform_internal_ip = _pd.get('IPAddress') or None
                            _psvc.save(update_fields=['platform_internal_ip', 'updated_at'])
                        break
            except Exception as exc:
                logger.debug("Could not cache platform IP for %s: %s", name, exc)

            with contextlib.suppress(Exception):
                green.stop(timeout=10)
            with contextlib.suppress(Exception):
                green.remove(force=True)

            if old_container is not None:
                with contextlib.suppress(Exception):
                    old_container.stop(timeout=10)
                try:
                    old_container.remove(force=True)
                except Exception as exc:
                    logger.warning(
                        "Blue-green promote: old backup cleanup failed (%s): %s",
                        backup_name or name,
                        exc,
                    )

            logger.info(
                "Blue-green promote: cutover complete for %s (public=%s)",
                name,
                is_public,
            )
            return promoted.id

        except Exception as exc:
            logger.error(
                "Blue-green promote failed for %s: %s. Attempting rollback restore.",
                name,
                exc,
            )
            if promoted is not None:
                with contextlib.suppress(Exception):
                    promoted.stop(timeout=5)
                with contextlib.suppress(Exception):
                    promoted.remove(force=True)

            if old_container is not None and backup_name:
                try:
                    old_container.reload()
                except docker.errors.NotFound:
                    logger.error(
                        "Blue-green rollback: previous live container %s "
                        "is gone — cannot restore. Creating emergency replacement.",
                        backup_name,
                    )
                    old_container = None
                except Exception as reload_exc:
                    logger.warning(
                        "Blue-green rollback: old container reload failed for %s: %s",
                        backup_name, reload_exc,
                    )
                if old_container is not None:
                    try:
                        if getattr(old_container, "name", "") != name:
                            old_container.rename(name)
                    except Exception as restore_exc:
                        raise RuntimeError(
                            "Promotion failed and previous live container could not be restored"
                        ) from restore_exc

                    # The backup container was stopped before promote to
                    # avoid a Traefik label conflict.  Start it again now
                    # that the (failed) promoted container has been removed.
                    try:
                        if old_container.status != "running":
                            old_container.start()
                    except Exception as start_exc:
                        logger.warning(
                            "Blue-green rollback: failed to start backup container %s: %s",
                            name, start_exc,
                        )

                    try:
                        if self.docker_client is None:
                            raise RuntimeError("Docker client unavailable")
                        net = self.docker_client.networks.get(network_name)
                        net.disconnect(old_container)
                        net.connect(old_container, aliases=[name, f"{name}.default.internal"])
                    except Exception as net_exc:
                        logger.warning(
                            "Blue-green promote restore alias update failed: %s",
                            net_exc,
                        )
                else:
                    # Emergency: rollback container is gone and promote failed.
                    # Attempt to recreate from the green image to avoid total loss.
                    logger.error(
                        "Blue-green promote: rollback container %s is gone and "
                        "promote failed. Attempting emergency recreate from green image.",
                        backup_name,
                    )
                    try:
                        # Emergency recreate: same dual-homing shape as the
                        # normal promote path (plain dict + network kwarg),
                        # so the emergency container is reachable both on
                        # the project bridge AND cross-project.
                        emergency_aliases = [name, f"{name}.default.internal"]
                        emergency_networks: dict[str, Any] = {
                            network_name: self.docker_client.api.create_endpoint_config(
                                aliases=emergency_aliases
                            )
                        }
                        try:
                            _emerg_bridge = ensure_platform_bridge()
                            if _emerg_bridge != network_name:
                                emergency_networks[_emerg_bridge] = (
                                    self.docker_client.api.create_endpoint_config(
                                        aliases=emergency_aliases
                                    )
                                )
                        except Exception:
                            pass
                        emergency = self.docker_client.containers.create(
                            image=image_ref,
                            name=name,
                            environment=green_env,
                            network=network_name,
                            networking_config=emergency_networks,
                            labels=live_labels,
                            restart_policy=rp,
                            command=green_cmd,
                            security_opt=["no-new-privileges:true", "apparmor:docker-default"],
                            cap_drop=["ALL"],
                            cap_add=["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"],
                            pids_limit=1024,
                        )
                        emergency.start()
                        logger.info(
                            "Emergency recreate succeeded for %s (container %s).",
                            name, emergency.id[:12],
                        )
                    except Exception as emergency_exc:
                        logger.error(
                            "Emergency recreate also failed for %s: %s",
                            name, emergency_exc,
                        )

            raise RuntimeError(f"Blue-green promote failed: {exc}") from exc

    def _wait_container_healthy(
        self, container_id: str, timeout_seconds: int = 240, poll_seconds: int = 5
    ) -> bool:
        """Wait for a container to reach 'healthy' or 'running' (no healthcheck) state."""
        import time as _time
        deadline = _time.monotonic() + timeout_seconds
        poll_count = 0
        while _time.monotonic() < deadline:
            try:
                if self.docker_client is None:
                    raise RuntimeError("Docker client unavailable")
                container = self.docker_client.containers.get(container_id)
                container.reload()
                state = container.attrs.get("State") or {}
                status = (state.get("Status") or "").lower()
                health = ((state.get("Health") or {}).get("Status") or "").lower()
                oom = state.get("OOMKilled", False)
            except Exception as exc:
                logger.debug("Health poll %d: lookup error: %s", poll_count, exc)
                _time.sleep(poll_seconds)
                poll_count += 1
                continue

            # Log every 6th poll (~30s) to avoid spam
            if poll_count % 6 == 0:
                logger.info(
                    "Health poll %d: status=%s health=%s oom=%s (id=%s)",
                    poll_count, status, health, oom, container_id[:12],
                )

            if status in {"exited", "dead"}:
                exit_code = state.get("ExitCode", "?")
                logger.warning(
                    "Container %s terminated: status=%s exit_code=%s oom=%s",
                    container_id[:12], status, exit_code, oom,
                )
                return False
            if health == "healthy":
                return True
            if health == "unhealthy":
                logger.warning(
                    "Container %s unhealthy after %d polls",
                    container_id[:12], poll_count,
                )
                return False
            # Still within start_period — keep waiting
            if health == "starting":
                logger.debug(
                    "Container %s health still starting (poll %d), continuing...",
                    container_id[:12], poll_count,
                )
                _time.sleep(poll_seconds)
                poll_count += 1
                continue
            # No healthcheck configured - running means ready
            if status == "running" and not health:
                return True

            _time.sleep(poll_seconds)
            poll_count += 1
        logger.warning(
            "Container %s health check timed out after %ds (%d polls). "
            "Last state: status=%s health=%s",
            container_id[:12], timeout_seconds, poll_count,
            status if 'status' in locals() else '?',
            health if 'health' in locals() else '?',
        )
        # If the container is still running and health is still in
        # start_period, consider it healthy — the app is up even if
        # Docker hasn't finished its first health probe yet.
        return bool(status in ("running",) and health in ("starting", "n/a", ""))

    def _deploy_k8s(self, name: str, image: str,
                    env: dict[str, str], cpu: int, memory: int,
                    replicas: int = 1, healthcheck: dict | None = None,
                    vpa_enabled: bool = True, **kwargs) -> str:
        raise NotImplementedError("Kubernetes deployment is not supported. Use Docker or a lite agent.")

    # --- Serverless Functions Implementation ---
    def deploy_function(self, function_name: str,
                        code_zip: str, handler: str, runtime: str) -> str:
        """
        Deploy a Serverless Function.

        Real Implementation:
        - Mounts the code path (assuming code_zip is a path to a directory/file).
        - Uses a generic runtime image to execute the handler.
        - Defaults to a simple HTTP wrapper for 'Hot Function' behavior.
        """
        logger.info(f"Deploying function {function_name} ({runtime})")

        # Runtime Mappings to standard images
        runtime_images = {
            'python3.9': 'python:3.9-slim',
            'nodejs18': 'node:18-alpine',
        }
        image = runtime_images.get(runtime, 'python:3.9-slim')

        # Ensure code path exists
        if not os.path.exists(code_zip):
            # In production, we'd pull from S3/Storage. For local, we assume a path.
            logger.warning(
                f"Code path {code_zip} does not exist. Using simulation mode.")
            code_mount = None
        else:
            code_mount = code_zip

        # Validate handler: must be valid Python/JS identifiers separated by dots
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$', handler):
            raise ValueError(
                f"Invalid handler format: {handler!r}. "
                "Must be module.function (e.g. 'main.handler')."
            )

        # Wrapper Command: Simple HTTP Server that imports handler
        # This is a 'poor man's' OpenFaaS watchdog
        if 'python' in runtime:
            # Assumes handler format: module.function_name
            module_name, func_name = handler.rsplit('.', 1)
            cmd = f"""
            pip install flask &&
            cat <<'SERVEREOF' > server.py
from flask import Flask, request
import {module_name}

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def handle():
    return str({module_name}.{func_name}(request.json or {{}}))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
SERVEREOF
            python server.py
            """
            entrypoint = ["/bin/sh", "-c", cmd]
        elif 'node' in runtime:
            module_part, func_part = handler.rsplit('.', 1)
            cmd = f"""
             npm install express &&
             node -e "
             const express = require('express');
             const app = express();
             app.use(express.json());
             const handler = require('./{module_part}');
             app.all('/', async (req, res) => {{
                 const result = await handler.{func_part}(req.body);
                 res.send(result);
             }});
             app.listen(8080, '0.0.0.0');
             "
             """
            entrypoint = ["/bin/sh", "-c", cmd]
        else:
            entrypoint = None

        env_vars = {
            "PORT": "8080",
            "PYTHONUNBUFFERED": "1"
        }

        volumes = []
        if code_mount:
            # Mount code to /app
            volumes.append(
                {'name': f'{function_name}-code', 'mount_path': '/app'})

        if self.docker_client:
            return self._deploy_docker_function(
                function_name, image, env_vars,
                volumes or [], entrypoint or [], code_mount or "",
            )

        raise RuntimeError("No local orchestrator available (Kubernetes deployment is not supported)")

    # pylint: disable=too-many-positional-arguments, R0917
    def _deploy_docker_function(self, name: str, image: str, env: dict[str, str],
                                volumes: list[dict], entrypoint: list[str], code_path: str) -> str:
        """Deploy function as a Docker container with code mount."""
        try:
            network_name = os.getenv('DOCKER_NETWORK', 'smsly-net')
            if not self.docker_client:
                raise RuntimeError("Docker client not available")
            try:
                self.docker_client.networks.get(network_name)
            except docker.errors.NotFound:
                self.docker_client.networks.create(network_name)

            try:
                c = self.docker_client.containers.get(name)
                c.remove(force=True)
            except docker.errors.NotFound:
                pass

            # Docker Volume Binding
            binds = {}
            if code_path:
                binds[code_path] = {'bind': '/app', 'mode': 'ro'}

            container = self.docker_client.containers.run(
                image,
                name=name,
                environment=env,
                detach=True,
                network=network_name,
                volumes=binds,
                working_dir='/app',
                entrypoint=entrypoint,
                mem_limit='128m',
                cpu_quota=10000,
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                cap_add=["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"],
            )
            return f"docker-function://{container.id}"
        except Exception as e:
            logger.error(f"Failed to deploy docker function: {e}")
            raise e

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        return f"local://{bucket_name}"

    def provision_database(self, db_name: str, engine: str,
                           version: str) -> str:
        if self.docker_client:
            network_name = os.getenv('DOCKER_NETWORK', 'smsly-net')
            try:
                self.docker_client.networks.get(network_name)
            except docker.errors.NotFound:
                self.docker_client.networks.create(
                    network_name, driver="bridge")

            # Generate secure random password
            db_password = secrets.token_urlsafe(48)
            db_user = "smsly_user"

            self.docker_client.containers.run(
                f"{engine}:{version}-alpine",
                name=f"db-{db_name}",
                environment={
                    "POSTGRES_PASSWORD": db_password,
                    "POSTGRES_USER": db_user,
                    "POSTGRES_DB": db_name
                },
                detach=True,
                network=network_name,
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                cap_add=["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"],
            )
            # Return connection URL with generated credentials
            logger.info(
                f"Database provisioned: db-{db_name} with secure credentials")
            return f"postgresql://{db_user}:{db_password}@db-{db_name}:5432/{db_name}"
        return "local-db-provisioned"

    def create_vpc(self, cidr_block: str) -> str:
        return "local-network"

    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        return "local-waf-simulated"

    def issue_ssl_cert(self, domain_name: str) -> str:
        return "local-self-signed-cert"

    def create_iam_role(self, role_name: str, policy: dict[str, Any]) -> str:
        return "local-role"

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        return f"local-secret://{secret_name}"

    def get_metrics(self, resource_id: str, metric_name: str,
                    start_time: str, end_time: str) -> list[dict]:
        return []
