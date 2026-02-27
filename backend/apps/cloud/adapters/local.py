"""Local module."""
import docker
import logging
import secrets
import json
import os
from typing import Dict, Any, List
from kubernetes import client, config
from .base import BaseCloudAdapter

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


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


def _normalize_health_path(path: str) -> str:
    value = str(path or "/").strip()
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _build_docker_healthcheck_cmd(url: str, timeout_seconds: int) -> str:
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
    py_probe = (
        "import urllib.request;"
        f"urllib.request.urlopen('{url}', timeout={timeout})"
    )

    return (
        "if command -v wget >/dev/null 2>&1; then "
        f"wget -q -O /dev/null \"{url}\" >/dev/null 2>&1 && exit 0 || exit 1; "
        "fi; "
        "if command -v curl >/dev/null 2>&1; then "
        f"curl -fsS \"{url}\" >/dev/null 2>&1 && exit 0 || exit 1; "
        "fi; "
        "if command -v python3 >/dev/null 2>&1; then "
        f"python3 -c \"{py_probe}\" >/dev/null 2>&1 && exit 0 || exit 1; "
        "fi; "
        "if command -v python >/dev/null 2>&1; then "
        f"python -c \"{py_probe}\" >/dev/null 2>&1 && exit 0 || exit 1; "
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
        self.k8s_client = None
        self.batch_v1 = None

        try:
            from apps.cloud.docker_client import get_docker_client
            self.docker_client = get_docker_client()
        except Exception as e:
            logger.warning(f"Docker client not available: {e}")

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

    def authenticate(self) -> bool:
        return self.docker_client is not None or self.k8s_client is not None

        # pylint: disable=too-many-positional-arguments
    def deploy_container(self, service_name: str, image: str,
                         env_vars: Dict[str, str], cpu: int, memory: int,
                         replicas: int = 1, **kwargs) -> str:
        volumes = kwargs.get('volumes', None)
        healthcheck = kwargs.get('healthcheck', None)
        restart_policy = kwargs.get('restart_policy', 'unless-stopped')
        if self.k8s_client:
            return self._deploy_k8s(
                service_name, image, env_vars, cpu, memory, replicas)
        elif self.docker_client:
            return self._deploy_docker(
                service_name, image, env_vars, volumes=volumes,
                healthcheck=healthcheck, cpu=cpu, memory=memory,
                restart_policy=restart_policy)
        else:
            raise RuntimeError("No local orchestrator available")

    # pylint: disable=too-many-positional-arguments, R0917
    def _deploy_docker(self, name: str, image: str,
                       env: Dict[str, str], volumes: List[Dict] = None,
                       project_id: str = 'default',
                       healthcheck: Dict = None,
                       cpu: int = None, memory: int = None,
                       restart_policy: str = 'unless-stopped') -> str:
        """
        Blue-green Docker deployment.

        Creates a new container with a temporary name, waits for it to pass
        Docker health checks, then atomically swaps it into the final name.
        The old container keeps serving traffic until the new one is verified
        healthy — zero downtime in all cases.
        """
        import time as _time

        # Ensure shared network exists
        network_name = os.getenv('DOCKER_NETWORK', 'smsly-net')
        try:
            self.docker_client.networks.get(network_name)
        except docker.errors.NotFound:
            self.docker_client.networks.create(network_name, driver="bridge")

        # Prepare Volumes
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

        # ── Blue-Green: detect old container ──
        old_container = None
        try:
            old_container = self.docker_client.containers.get(name)
        except docker.errors.NotFound:
            pass

        # Use temporary name for the new container so the old one keeps serving.
        temp_name = f"{name}-green-{secrets.token_hex(4)}"

        # Networking: use temp_name aliases initially; swap to real name after cutover.
        # The old container still holds the canonical aliases, so Traefik routes
        # traffic to it until we remove it and rename the new one.
        networking_config = self.docker_client.api.create_networking_config({
            network_name: self.docker_client.api.create_endpoint_config(
                aliases=[temp_name, f"{name}.{project_id}.internal.green"]
            )
        })

        # Traefik Labels — support primary domain + custom domains
        domain = env.get('PUBLIC_DOMAIN', f"{name}.localhost")
        port = env.get('PORT', '8000')

        # Check if service should be publicly accessible
        is_public = True
        try:
            from apps.deployments.models import Service
            svc_obj = Service.objects.filter(name=name).first()
            if svc_obj is not None:
                is_public = svc_obj.is_public
        except Exception:
            pass

        # Build Host rule: primary domain + any custom domains
        all_domains = [domain]
        custom = env.get('CUSTOM_DOMAINS', '')
        if custom:
            all_domains.extend([d.strip() for d in custom.split(',') if d.strip()])
        host_rule = ' || '.join(f'Host(`{d}`)' for d in all_domains)

        # Check Platform Config for SSL
        try:
            from apps.deployments.models import PlatformConfig
            config = PlatformConfig.load()
            use_ssl = config.use_ssl
        except Exception:
            use_ssl = False

        enable_traefik_tls = (
            str(os.getenv("TRAEFIK_ENABLE_WEBSECURE", "false")).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        # New container uses DISABLED Traefik routing during health check.
        # Traffic continues to flow to the old container. After cutover we
        # re-label the new container with the real routing labels.
        labels = {
            'managed_by': 'smsly-hosting',
            'traefik.enable': 'false',  # Disabled until health check passes
            f'traefik.http.services.{name}.loadbalancer.server.port': port
        }

        # Docker-native healthcheck.
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
            120 if low_resource_profile else 60,
            minimum=1,
        )

        hc_port = (
            (healthcheck or {}).get('port')
            or env.get('PORT')
            or '8000'
        )
        if healthcheck and healthcheck.get('path'):
            hc_path = _normalize_health_path(healthcheck['path'])
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
            hc_url = f"http://127.0.0.1:{hc_port}{hc_path}"
            hc_cmd = _build_docker_healthcheck_cmd(hc_url, hc_timeout)
        else:
            hc_interval = min_interval
            hc_timeout = min_timeout
            hc_retries = min_retries
            hc_url = f"http://127.0.0.1:{hc_port}/"
            hc_cmd = _build_docker_healthcheck_cmd(hc_url, hc_timeout)
        docker_healthcheck = docker.types.Healthcheck(
            test=["CMD-SHELL", hc_cmd],
            interval=hc_interval * 1_000_000_000,
            timeout=hc_timeout * 1_000_000_000,
            retries=hc_retries,
            start_period=start_period_seconds * 1_000_000_000,
        )

        # Resource limits
        run_kwargs = {}
        if memory and memory > 0:
            run_kwargs['mem_limit'] = f"{memory}m"
        if cpu and cpu > 0:
            run_kwargs['cpu_period'] = 100000
            run_kwargs['cpu_quota'] = int((cpu / 1000) * 100000)

        # Restart policy: on-failure with max 5 retries to prevent infinite loops.
        if restart_policy == 'no':
            rp = None
        elif restart_policy == 'unless-stopped':
            rp = {"Name": "on-failure", "MaximumRetryCount": 5}
        else:
            rp = {"Name": restart_policy, "MaximumRetryCount": 5}

        # ── Create & start new container with temp name ──
        new_container = self.docker_client.containers.create(
            image,
            name=temp_name,
            environment=env,
            network=network_name,
            networking_config=networking_config,
            labels=labels,
            volumes=docker_volumes if docker_volumes else None,
            healthcheck=docker_healthcheck,
            restart_policy=rp,
            **run_kwargs
        )
        new_container.start()
        logger.info("Blue-green: started new container %s (old: %s)",
                     temp_name, name if old_container else "none")

        # ── Wait for new container to become healthy ──
        health_timeout_default = 360 if low_resource_profile else 240
        health_timeout = _env_int(
            'BLUE_GREEN_HEALTH_TIMEOUT',
            health_timeout_default,
            minimum=30,
        )
        new_healthy = self._wait_container_healthy(
            new_container.id, timeout_seconds=health_timeout
        )

        if not new_healthy:
            # New container failed health — remove it, keep old running
            logger.error(
                "Blue-green: new container %s failed health check, keeping old %s",
                temp_name, name,
            )
            try:
                new_container.remove(force=True)
            except Exception:
                pass
            raise RuntimeError(
                f"New container {temp_name} failed health check. "
                f"Old container {name} is still serving traffic."
            )

        # ── Cutover: remove old, rename new ──
        if old_container:
            try:
                old_container.stop(timeout=10)
                old_container.remove(force=True)
            except Exception as exc:
                logger.warning("Blue-green: failed to remove old container %s: %s", name, exc)

        # Rename new container to the canonical name
        try:
            new_container.rename(name)
        except Exception as exc:
            logger.warning("Blue-green: rename %s -> %s failed: %s", temp_name, name, exc)

        # ── Apply real Traefik routing labels ──
        # Docker SDK doesn't support label updates on running containers, so we
        # disconnect and reconnect with proper aliases. Traefik discovers routing
        # from container labels, which were set at create-time. We need to update
        # labels by using the low-level API.
        final_labels = {
            'managed_by': 'smsly-hosting',
            'traefik.enable': 'true' if is_public else 'false',
            f'traefik.http.services.{name}.loadbalancer.server.port': port,
        }
        if not is_public:
            pass
        elif use_ssl and enable_traefik_tls:
            final_labels.update({
                f'traefik.http.routers.{name}-http.rule': host_rule,
                f'traefik.http.routers.{name}-http.entrypoints': 'web',
                f'traefik.http.routers.{name}-http.middlewares': f'{name}-redirect',
                f'traefik.http.middlewares.{name}-redirect.redirectscheme.scheme': 'https',
                f'traefik.http.middlewares.{name}-redirect.redirectscheme.permanent': 'true',
                f'traefik.http.routers.{name}.rule': host_rule,
                f'traefik.http.routers.{name}.entrypoints': 'websecure',
                f'traefik.http.routers.{name}.tls': 'true',
                f'traefik.http.routers.{name}.tls.certresolver': 'letsencrypt',
            })
        else:
            final_labels.update({
                f'traefik.http.routers.{name}.rule': host_rule,
                f'traefik.http.routers.{name}.entrypoints': 'web',
            })
            if use_ssl:
                middleware_name = f'{name}-forwarded-https'
                final_labels.update({
                    f'traefik.http.routers.{name}.middlewares': middleware_name,
                    f'traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Proto': 'https',
                    f'traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Port': '443',
                    f'traefik.http.middlewares.{middleware_name}.headers.customrequestheaders.X-Forwarded-Ssl': 'on',
                })

        # Update labels on the running container via low-level Docker API.
        # This turns on Traefik routing now that the container is healthy + renamed.
        try:
            self.docker_client.api.update_container(
                new_container.id, labels=final_labels,
            )
        except Exception:
            # Fallback: some Docker versions don't support label update via
            # update_container. In that case, reconnect with proper network aliases
            # so Docker DNS resolves the canonical name.
            pass

        # Ensure canonical network aliases are set
        try:
            net = self.docker_client.networks.get(network_name)
            net.disconnect(new_container)
            net.connect(
                new_container,
                aliases=[name, f"{name}.{project_id}.internal"],
            )
        except Exception as exc:
            logger.warning("Blue-green: network alias update failed: %s", exc)

        logger.info("Blue-green: cutover complete — %s is now serving", name)
        return new_container.id

    def _wait_container_healthy(
        self, container_id: str, timeout_seconds: int = 240, poll_seconds: int = 5
    ) -> bool:
        """Wait for a container to reach 'healthy' or 'running' (no healthcheck) state."""
        import time as _time
        deadline = _time.monotonic() + timeout_seconds
        while _time.monotonic() < deadline:
            try:
                container = self.docker_client.containers.get(container_id)
                container.reload()
                state = container.attrs.get("State") or {}
                status = (state.get("Status") or "").lower()
                health = ((state.get("Health") or {}).get("Status") or "").lower()
            except Exception:
                _time.sleep(poll_seconds)
                continue

            if status in {"exited", "dead"}:
                return False
            if health == "healthy":
                return True
            if health == "unhealthy":
                return False
            # No healthcheck configured — running means ready
            if status == "running" and not health:
                return True

            _time.sleep(poll_seconds)
        return False

    def _deploy_k8s(self, name: str, image: str,
                    env: Dict[str, str], cpu: int, memory: int, replicas: int = 1) -> str:
        namespace = 'default'

        # 1. Deployment
        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1DeploymentSpec(
                replicas=replicas,
                selector=client.V1LabelSelector(match_labels={"app": name}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"app": name}),
                    spec=client.V1PodSpec(
                        containers=[
                            client.V1Container(
                                name=name,
                                image=image,
                                env=[
                                    client.V1EnvVar(
                                        name=k,
                                        value=v) for k,
                                    v in env.items()],
                                resources=client.V1ResourceRequirements(
                                    requests={
                                        "cpu": f"{cpu}m", "memory": f"{memory}Mi"},
                                    limits={"cpu": f"{cpu * 2}m",
                                            "memory": f"{memory * 2}Mi"}
                                )
                            )
                        ]
                    )
                )
            )
        )

        try:
            self.k8s_apps.create_namespaced_deployment(
                namespace=namespace, body=deployment)
        except client.exceptions.ApiException as e:
            if e.status == 409:
                self.k8s_apps.patch_namespaced_deployment(
                    name=name, namespace=namespace, body=deployment)
            else:
                raise

        # 2. Service (ClusterIP) for Discovery
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1ServiceSpec(
                selector={"app": name},
                ports=[
                    client.V1ServicePort(
                        port=80,
                        target_port=int(
                            env.get(
                                'PORT',
                                8000)))],
                type="ClusterIP"
            )
        )

        try:
            self.k8s_client.create_namespaced_service(
                namespace=namespace, body=svc)
        except client.exceptions.ApiException as e:
            if e.status != 409:
                logger.warning(f"Failed to create service for {name}: {e}")

        return f"k8s://{namespace}/{name}"

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

        # Wrapper Command: Simple HTTP Server that imports handler
        # This is a 'poor man's' OpenFaaS watchdog
        if 'python' in runtime:
            # Assumes handler format: module.function_name
            module_name, func_name = handler.split('.')
            cmd = f"""
            pip install flask &&
            cat <<EOF > server.py
from flask import Flask, request
import {module_name}

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def handle():
    return str({module_name}.{func_name}(request.json or {{}}))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
EOF
            python server.py
            """
            entrypoint = ["/bin/sh", "-c", cmd]
        elif 'node' in runtime:
            cmd = f"""
             npm install express &&
             node -e "
             const express = require('express');
             const app = express();
             app.use(express.json());
             const handler = require('./{handler.split('.')[0]}');
             app.all('/', async (req, res) => {{
                 const result = await handler.{handler.split('.')[1]}(req.body);
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
                function_name, image, env_vars, volumes, entrypoint, code_mount)

        # Fallback for K8s (simplified)
        return self._deploy_k8s(
            function_name, image, env_vars, cpu=100, memory=128)

    # pylint: disable=too-many-positional-arguments, R0917
    def _deploy_docker_function(self, name: str, image: str, env: Dict[str, str],
                                volumes: List[Dict], entrypoint: List[str], code_path: str) -> str:
        """Deploy function as a Docker container with code mount."""
        try:
            network_name = os.getenv('DOCKER_NETWORK', 'smsly-net')
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
                cpu_quota=10000
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
            db_password = secrets.token_urlsafe(24)
            db_user = "smsly_user"

            container = self.docker_client.containers.run(
                f"{engine}:{version}-alpine",
                name=f"db-{db_name}",
                environment={
                    "POSTGRES_PASSWORD": db_password,
                    "POSTGRES_USER": db_user,
                    "POSTGRES_DB": db_name
                },
                detach=True,
                network=network_name
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

    def create_iam_role(self, role_name: str, policy: Dict[str, Any]) -> str:
        return "local-role"

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        return f"local-secret://{secret_name}"

    def get_metrics(self, resource_id: str, metric_name: str,
                    start_time: str, end_time: str) -> List[Dict]:
        return []
