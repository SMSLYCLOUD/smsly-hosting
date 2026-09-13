"""
Envoy Sidecar Service
======================
Manages Envoy sidecar lifecycle for transparent mTLS on user services.

The Envoy sidecar handles:
- Inbound mTLS termination (validates caller's SPIFFE identity)
- Outbound mTLS origination (presents this service's SPIFFE identity)
- Dynamic certificate rotation via SPIRE SDS
- L7 authorization policy enforcement via RBAC

Usage:
    from apps.mtls.services.envoy_sidecar import EnvoySidecar

    # Generate config for a service
    config = EnvoySidecar.generate_config(service, mtls_config)

    # Inject sidecar into running service
    EnvoySidecar.inject_sidecar(service)

    # Remove sidecar
    EnvoySidecar.remove_sidecar(service)

    # Check sidecar status
    status = EnvoySidecar.get_sidecar_status(service)
"""

import logging
import os
import re
import shutil
import time

logger = logging.getLogger(__name__)

ENVOY_IMAGE = os.getenv("ENVOY_SIDECAR_IMAGE", "ghcr.io/smsly/envoy-spire-sidecar:latest")
ENVOY_ADMIN_PORT = 9901
ENVOY_INBOUND_PORT = 80
ENVOY_OUTBOUND_PORT = 8080

# Build context for (re)building the sidecar image when the platform
# registry does not have it. The backend image does NOT contain the
# repo, so this must be a host-mounted path: compose mounts
# ./infrastructure/envoy -> /opt/envoy-build (ro) on backend/workers.
ENVOY_BUILD_CONTEXT_CANDIDATES = (
    "/opt/envoy-build",
    "/opt/smsly-hosting/infrastructure/envoy",
)

# Volume mounts for SPIRE agent socket and SVIDs
SPIRE_AGENT_SOCKET_VOLUME = "spire-ecosystem-agent-socket"
SPIRE_SVIDS_VOLUME = "spire-ecosystem-agent-svids"
SPIRE_AGENT_SOCKET_CONTAINER_PATH = "/opt/spire/run"
SPIRE_SVIDS_CONTAINER_PATH = "/opt/spire/svids"


class EnvoySidecar:
    """Manages Envoy sidecar lifecycle for a service."""

    @staticmethod
    def _split_image_ref(image: str) -> tuple[str, str, str]:
        """Split an image ref into (registry_host, repository, tag).

        ``registry:5000/smsly/x:latest`` -> (``registry:5000``,
        ``registry:5000/smsly/x``, ``latest``). Unqualified names yield
        an empty host and tag ``latest``.
        """
        tag = "latest"
        repository = image
        host = ""
        if ":" in repository.rsplit("/", 1)[-1]:
            repository, _, tag = repository.rpartition(":")
            if "/" in tag:
                repository, tag = image, "latest"
        first, _, _rest = repository.partition("/")
        if _rest and (":" in first or "." in first or first == "localhost"):
            host = first
        return host, repository, tag

    @staticmethod
    def _platform_registry_auth() -> tuple[str, str, str]:
        """Return (registry_host, username, password) for the platform registry.

        Source of truth is the ScopedRegistry chain with the PlatformConfig
        fallback (``smsly-registry`` / ``REGISTRY_PASSWORD``) — the same
        credential family the pipeline uses for pushes. Empty user/password
        when nothing is configured (callers must handle that explicitly).
        """
        try:
            from apps.deployments.models.registry_scope import ScopedRegistry

            info = ScopedRegistry.resolve_registry_credentials(None) or {}
            url = str(info.get("url") or "").split("://")[-1].rstrip("/")
            return (
                url or "registry:5000",
                str(info.get("username") or ""),
                str(info.get("password") or ""),
            )
        except Exception as exc:
            logger.debug("Platform registry credential resolution failed: %s", exc)
            return "registry:5000", "", ""

    @staticmethod
    def _pull_sidecar_image(client, image: str) -> None:
        """Ensure *image* is present in the daemon, authenticating first.

        The platform registry enforces htpasswd auth globally, so an
        anonymous pull always 401s ("no basic auth credentials") — the
        exact failure the mTLS repair reported for every service. Raises
        ``docker.errors.ImageNotFound`` when the image cannot be
        obtained (missing repo, bad credentials, registry down) so
        callers keep their existing tolerate-and-warn contract.
        """
        import docker

        try:
            client.images.get(image)
            return
        except Exception:
            pass

        logger.info("Pulling Envoy sidecar image %s", image)
        _host, _repository, _tag = EnvoySidecar._split_image_ref(image)
        _url, _user, _pwd = EnvoySidecar._platform_registry_auth()
        auth_config = (
            {"username": _user, "password": _pwd} if _user and _pwd else None
        )
        try:
            client.images.pull(_repository, tag=_tag, auth_config=auth_config)
            return
        except docker.errors.ImageNotFound:
            raise
        except docker.errors.APIError as exc:
            status = getattr(exc, "status_code", None) or getattr(exc, "response", None) and getattr(exc.response, "status_code", None)
            detail = str(exc)
            if status == 401 or "no basic auth credentials" in detail.lower():
                if not auth_config:
                    raise docker.errors.ImageNotFound(
                        f"Registry {(_host or _url)} requires authentication but no "
                        "platform registry credential is configured "
                        "(PlatformConfig registry_user/registry_password). "
                        f"Original error: {exc}"
                    ) from exc
                raise docker.errors.ImageNotFound(
                    f"Registry authentication failed for {image} — the platform "
                    "credential was rejected. Verify it matches the registry "
                    f"htpasswd entry. Original error: {exc}"
                ) from exc
            if status == 404 or "manifest unknown" in detail.lower() or "not found" in detail.lower():
                raise docker.errors.ImageNotFound(
                    f"Sidecar image {image} is not in the platform registry. "
                    f"Original error: {exc}"
                ) from exc
            raise

    @staticmethod
    def _build_and_push_sidecar_image(client, image: str) -> None:
        """Build the sidecar image from the mounted build context and push it.

        Self-heals a registry that lost (or never received) the image —
        e.g. fresh installs where the installer ran before the registry
        was up. Raises ``docker.errors.ImageNotFound`` when no build
        context is available or the build/push fails.
        """
        import docker

        build_dir = ""
        for candidate in ENVOY_BUILD_CONTEXT_CANDIDATES:
            try:
                if os.path.isfile(os.path.join(candidate, "Dockerfile")):
                    build_dir = candidate
                    break
            except Exception:
                continue
        if not build_dir:
            raise docker.errors.ImageNotFound(
                "No Envoy build context available "
                f"(looked in {', '.join(ENVOY_BUILD_CONTEXT_CANDIDATES)}). "
                "Build it on the host: "
                "docker build -t registry:5000/smsly/envoy-spire-sidecar:latest "
                "infrastructure/envoy && docker push 127.0.0.1:5000/smsly/envoy-spire-sidecar:latest"
            )
        _host, _repository, _tag = EnvoySidecar._split_image_ref(image)
        _url, _user, _pwd = EnvoySidecar._platform_registry_auth()
        auth_config = (
            {"username": _user, "password": _pwd} if _user and _pwd else None
        )
        logger.info("Building Envoy sidecar image %s from %s", image, build_dir)
        try:
            client.images.build(
                path=build_dir, tag=image, rm=True, pull=True,
                timeout=max(int(getattr(client, "timeout", 600) or 600), 600),
            )
        except Exception as exc:
            raise docker.errors.ImageNotFound(
                f"Envoy sidecar image build failed in {build_dir}: {exc}"
            ) from exc
        try:
            client.images.push(_repository, tag=_tag, auth_config=auth_config)
        except Exception as exc:
            raise docker.errors.ImageNotFound(
                f"Envoy sidecar image push of {image} failed: {exc}"
            ) from exc

    @staticmethod
    def ensure_sidecar_image(client, image: str | None = None) -> str:
        """Ensure the sidecar image exists locally, building it if needed.

        Pull (authenticated) -> build+push fallback -> verify. Returns
        the image ref. Raises ``docker.errors.ImageNotFound`` only when
        all avenues fail, preserving the tolerate-and-warn contract of
        ``inject_sidecar`` callers.
        """
        import docker

        image = image or ENVOY_IMAGE
        try:
            EnvoySidecar._pull_sidecar_image(client, image)
            return image
        except docker.errors.ImageNotFound as pull_exc:
            logger.warning("Sidecar pull failed for %s (%s) — attempting build", image, pull_exc)
        EnvoySidecar._build_and_push_sidecar_image(client, image)
        EnvoySidecar._pull_sidecar_image(client, image)
        return image

    @staticmethod
    def generate_config(service, mtls_config):
        """
        Generate Envoy YAML config for a service.

        Args:
            service: The Service model instance
            mtls_config: The MtlsConfig model instance

        Returns:
            str: Complete Envoy YAML configuration
        """
        template_path = os.getenv("ENVOY_TEMPLATE_PATH") or os.path.join(
            os.path.dirname(__file__),
            "..", "..", "..", "..", "..",
            "infrastructure", "envoy", "envoy.yaml.template",
        )

        try:
            with open(template_path, "r") as f:
                template = f.read()
        except FileNotFoundError:
            raise RuntimeError(f"Envoy template not found at {template_path}")

        # Get service port
        app_port = service.internal_port or 8000

        # Get SPIRE agent socket path
        spire_socket = os.getenv(
            "SPIFFE_ENDPOINT_SOCKET",
            "unix:///opt/spire/run/agent.sock",
        )
        # Strip unix:// prefix for Envoy UDS config
        socket_path = spire_socket.replace("unix://", "")

        # Generate SPIFFE ID
        spiffe_id = mtls_config.spiffe_id or f"spiffe://ecosystem.local/service/{service.name}"
        trust_domain = mtls_config.trust_domain or "ecosystem.local"

        # Replace placeholders
        config = template
        config = config.replace("{{APP_PORT}}", str(app_port))
        config = config.replace("{{TRUST_DOMAIN}}", trust_domain)
        config = config.replace("{{SERVICE_NAME}}", service.name)
        config = config.replace("{{SPIRE_AGENT_SOCKET}}", socket_path)

        return config

    @staticmethod
    def get_sidecar_name(service):
        """Get the container name for a service's Envoy sidecar."""
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "", service.name)[:80]
        return f"envoy-{safe_name}"

    @staticmethod
    def inject_sidecar(service):
        """
        Inject Envoy sidecar container alongside a running service.

        The sidecar uses Docker network_mode: service:{main_container}
        so it shares the network namespace with the application container.

        Args:
            service: The Service model instance

        Returns:
            dict: Sidecar container info
        """
        from apps.cloud.docker_client import get_docker_client
        from apps.mtls.models import MtlsConfig

        client = get_docker_client()

        # Ensure the sidecar image is available locally. Fresh hosts may
        # never have built it and the platform registry enforces auth, so
        # pull authenticated (platform credential) with a build+push
        # fallback. A missing image raises ImageNotFound here so callers
        # can decide (deploy continues with a warning; the repair
        # endpoint reports it). Previously this pulled anonymously, which
        # always 401'd, and the 2026-09-11 404s killed whole deploys.
        EnvoySidecar.ensure_sidecar_image(client, ENVOY_IMAGE)

        mtls_config = service.mtls_config

        sidecar_name = EnvoySidecar.get_sidecar_name(service)

        # Check if sidecar already exists
        try:
            existing = client.containers.get(sidecar_name)
            if existing.status == "running":
                logger.info("Envoy sidecar already running for %s", service.name)
                return {"status": "already_running", "name": sidecar_name}
            else:
                # Remove stopped sidecar
                existing.remove(force=True)
        except Exception:
            pass

        # Find the main service container
        main_container = EnvoySidecar._find_main_container(client, service)
        if not main_container:
            raise RuntimeError(f"No running container found for service {service.name}")

        # Generate Envoy config
        config = EnvoySidecar.generate_config(service, mtls_config)

        # Write config to a temp file and mount it
        config_dir = os.getenv("ENVOY_CONFIG_DIR", "/opt/smsly-hosting/builds")
        os.makedirs(config_dir, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "", service.name)[:80]
        config_path = os.path.join(config_dir, f"envoy-{safe_name}.yaml")
        if os.path.isdir(config_path):
            shutil.rmtree(config_path)
        with open(config_path, "w") as f:
            f.write(config)

        try:
            from apps.deployments.services.mtls_integration import resolve_spire_volume_name
            socket_volume = resolve_spire_volume_name(SPIRE_AGENT_SOCKET_VOLUME)
            svids_volume = resolve_spire_volume_name(SPIRE_SVIDS_VOLUME)
            container = client.containers.run(
                image=ENVOY_IMAGE,
                name=sidecar_name,
                detach=True,
                restart_policy={"Name": "unless-stopped"},
                # Share network namespace with main container
                # The Docker API accepts container:<id> for namespace sharing;
                # service:<name> is a Compose-only form and fails via the
                # socket proxy during runtime injection.
                network_mode=f"container:{main_container.id}",
                # Keep the sidecar process in the host PID namespace so the
                # host-PID SPIRE agent can resolve its Workload API caller.
                # Network namespace remains shared with the app container.
                pid_mode="host",
                labels={
                    "managed_by": "smsly-hosting",
                    "envoy_sidecar": "true",
                    "smsly.blue_green.canonical_name": service.name,
                    "com.paas.service": service.name,
                    "com.paas.mtls": "true",
                    "com.paas.spiffe_id": mtls_config.spiffe_id or f"spiffe://{mtls_config.trust_domain}/service/{service.name}",
                },
                environment={
                    "SPIFFE_TRUST_DOMAIN": mtls_config.trust_domain or "ecosystem.local",
                    "SPIFFE_ENDPOINT_SOCKET": "unix:///opt/spire/run/agent.sock",
                    "SERVICE_NAME": service.name,
                    "APP_PORT": str(service.internal_port or 8000),
                },
                volumes={
                     config_path: {"bind": "/etc/envoy/envoy.yaml", "mode": "ro"},
                     "/opt/smsly-hosting/builds/envoy.yaml.template": {
                         "bind": "/etc/envoy/envoy.yaml.template", "mode": "ro",
                     },
                     socket_volume: {
                        "bind": SPIRE_AGENT_SOCKET_CONTAINER_PATH,
                        "mode": "ro",
                    },
                     svids_volume: {
                        "bind": SPIRE_SVIDS_CONTAINER_PATH,
                        "mode": "ro",
                    },
                },
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                mem_limit="64m",
                nano_cpus=int(0.25e9),  # 0.25 CPU
                pids_limit=64,
            )

            logger.info(
                "Injected Envoy sidecar %s for service %s",
                sidecar_name,
                service.name,
            )
            return {
                "status": "injected",
                "name": sidecar_name,
                "container_id": container.id[:12],
            }

        finally:
            # Keep the host-visible config while the sidecar is running.
            try:
                if not os.getenv("ENVOY_CONFIG_DIR"):
                    os.unlink(config_path)
            except Exception:
                pass

    @staticmethod
    def remove_sidecar(service):
        """
        Remove Envoy sidecar container for a service.

        Args:
            service: The Service model instance

        Returns:
            dict: Removal status
        """
        from apps.cloud.docker_client import get_docker_client

        client = get_docker_client()
        sidecar_name = EnvoySidecar.get_sidecar_name(service)

        try:
            container = client.containers.get(sidecar_name)
            container.reload()
            container.stop(timeout=5)
            container.remove(force=True)
            logger.info("Removed Envoy sidecar %s for service %s", sidecar_name, service.name)
            return {"status": "removed", "name": sidecar_name}
        except Exception as exc:
            logger.warning("Could not remove sidecar %s: %s", sidecar_name, exc)
            return {"status": "not_found", "name": sidecar_name}

    @staticmethod
    def remove_orphan_sidecar(service):
        """
        Remove a non-running Envoy sidecar corpse (Created/Exited/Dead).

        A running sidecar is NEVER touched — it may be serving mesh
        traffic even when the DB row disagrees. Missing container and
        unreachable daemon degrade to a status dict, never raise.
        """
        from apps.cloud.docker_client import get_docker_client

        sidecar_name = EnvoySidecar.get_sidecar_name(service)
        try:
            client = get_docker_client()
            try:
                container = client.containers.get(sidecar_name)
            except Exception:
                return {"status": "not_found", "name": sidecar_name}
            try:
                container.reload()
            except Exception:
                pass
            if getattr(container, "status", "") == "running":
                return {"status": "running_kept", "name": sidecar_name}
            try:
                try:
                    container.stop(timeout=5)
                except Exception:
                    pass
                container.remove(force=True)
            except Exception:
                # Already gone between reload and remove — desired state.
                pass
            logger.info("Removed orphan Envoy sidecar %s for service %s",
                        sidecar_name, service.name)
            return {"status": "removed", "name": sidecar_name}
        except Exception as exc:
            logger.debug("Orphan sidecar cleanup skipped for %s: %s",
                         service.name, exc)
            return {"status": "unknown", "name": sidecar_name}

    @staticmethod
    def get_sidecar_status(service):
        """
        Check if the Envoy sidecar is running and healthy.

        Args:
            service: The Service model instance

        Returns:
            dict: Sidecar status information
        """
        from apps.cloud.docker_client import get_docker_client

        try:
            # Client acquisition is inside the try: without a reachable
            # daemon get_docker_client() itself raises, and the status
            # endpoint must degrade to "not_found", never 500.
            # sidecar_name stays outside: the except handler needs it and
            # it never touches Docker.
            sidecar_name = EnvoySidecar.get_sidecar_name(service)
            client = get_docker_client()
            container = client.containers.get(sidecar_name)

            # Check health via Envoy admin API
            healthy = False
            if container.status == "running":
                try:
                    # Use docker exec to check health
                    result = container.exec_run(
                        ["/bin/sh", "-c", "curl -fsS http://127.0.0.1:9901/ready"],
                        demux=False,
                    )
                    healthy = result.exit_code == 0
                except Exception:
                    pass

            return {
                "name": sidecar_name,
                "status": container.status,
                "healthy": healthy,
                "container_id": container.id[:12] if container else None,
                "image": container.image.tags[0] if container.image else None,
                "started_at": container.attrs.get("State", {}).get("StartedAt"),
            }
        except Exception:
            return {
                "name": sidecar_name,
                "status": "not_found",
                "healthy": False,
                "container_id": None,
                "image": None,
                "started_at": None,
            }

    @staticmethod
    def wait_sidecar_ready(service, timeout_seconds: int = 120) -> bool:
        """Wait until the sidecar is serving with an issued SVID.

        Polls Envoy admin /ready, then verifies /certs actually carries
        this service's SPIFFE identity. Deployments must gate go-live on
        this — otherwise traffic hits Envoy before SDS delivers the
        identity and mTLS handshakes fail.
        """
        from apps.cloud.docker_client import get_docker_client

        client = get_docker_client()
        sidecar_name = EnvoySidecar.get_sidecar_name(service)
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "", service.name)[:80]
        want = f"service/{safe_name}"
        deadline = time.time() + max(10, int(timeout_seconds or 120))
        last_state = "unknown"
        while time.time() < deadline:
            try:
                container = client.containers.get(sidecar_name)
                if getattr(container, "status", "") != "running":
                    last_state = f"container {getattr(container, 'status', '?')}"
                    time.sleep(5)
                    continue
                ready = container.exec_run(
                    ["/bin/sh", "-c", "curl -fsS --max-time 5 http://127.0.0.1:9901/ready"],
                    demux=False,
                )
                if ready.exit_code != 0:
                    last_state = "admin not ready"
                    time.sleep(5)
                    continue
                certs = container.exec_run(
                    ["/bin/sh", "-c", "curl -fsS --max-time 5 http://127.0.0.1:9901/certs"],
                    demux=False,
                )
                output = certs.output
                if isinstance(output, (bytes, bytearray)):
                    output = output.decode(errors="replace")
                if certs.exit_code == 0 and want in (output or ""):
                    logger.info("Envoy sidecar ready with SVID for %s", service.name)
                    return True
                last_state = "SVID not issued yet"
            except Exception as exc:
                last_state = str(exc)[:120]
            time.sleep(5)
        logger.warning(
            "Envoy sidecar not ready for %s after %ss (last: %s)",
            service.name, timeout_seconds, last_state,
        )
        return False

    @staticmethod
    def _find_main_container(client, service):
        """Find the main container for a service."""
        containers = client.containers.list(
            filters={"label": "managed_by=smsly-hosting"},
        )
        for ctr in containers:
            labels = ctr.labels or {}
            if labels.get("smsly.blue_green.canonical_name") == service.name:
                if not labels.get("envoy_sidecar"):
                    return ctr
        return None

    @staticmethod
    def inject_sidecar_compose(service, compose_data):
        """
        Inject Envoy sidecar into Docker Compose data.

        Used by the deployment pipeline to add sidecar to compose files.

        Args:
            service: The Service model instance
            compose_data: dict - parsed compose YAML

        Returns:
            dict: Modified compose data with sidecar service added
        """
        from apps.mtls.models import MtlsConfig

        try:
            mtls_config = service.mtls_config
            if not mtls_config.enabled:
                return compose_data
        except MtlsConfig.DoesNotExist:
            return compose_data

        sidecar_name = EnvoySidecar.get_sidecar_name(service)
        app_port = service.internal_port or 8000

        # Add sidecar service
        if "services" not in compose_data:
            compose_data["services"] = {}

        compose_data["services"][sidecar_name] = {
            "image": ENVOY_IMAGE,
            "restart": "unless-stopped",
            "network_mode": f"service:{service.name}",
            "environment": {
                "SPIFFE_TRUST_DOMAIN": mtls_config.trust_domain or "ecosystem.local",
                "SPIFFE_ENDPOINT_SOCKET": "unix:///opt/spire/run/agent.sock",
                "SERVICE_NAME": service.name,
                "APP_PORT": str(app_port),
            },
            "volumes": [
                f"{SPIRE_AGENT_SOCKET_VOLUME}:{SPIRE_AGENT_SOCKET_CONTAINER_PATH}:ro",
                f"{SPIRE_SVIDS_VOLUME}:{SPIRE_SVIDS_CONTAINER_PATH}:ro",
            ],
            "security_opt": ["no-new-privileges:true"],
            "cap_drop": ["ALL"],
            "mem_limit": "64m",
            "cpus": 0.25,
            "pids_limit": 64,
            "labels": {
                "managed_by": "smsly-hosting",
                "envoy_sidecar": "true",
                "smsly.blue_green.canonical_name": service.name,
            },
            "depends_on": {
                service.name: {
                    "condition": "service_started",
                },
            },
        }

        # Ensure SPIRE volumes are declared
        if "volumes" not in compose_data:
            compose_data["volumes"] = {}

        compose_data["volumes"][SPIRE_AGENT_SOCKET_VOLUME] = {
            "external": True,
            "name": SPIRE_AGENT_SOCKET_VOLUME,
        }
        compose_data["volumes"][SPIRE_SVIDS_VOLUME] = {
            "external": True,
            "name": SPIRE_SVIDS_VOLUME,
        }

        return compose_data
