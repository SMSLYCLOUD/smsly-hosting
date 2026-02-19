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
        # Ensure shared network exists
        network_name = os.getenv('DOCKER_NETWORK', 'smsly-net')
        try:
            self.docker_client.networks.get(network_name)
        except docker.errors.NotFound:
            self.docker_client.networks.create(network_name, driver="bridge")

        # Mesh / Service Discovery
        # IMPORTANT: Use networking_config with containers.create() + container.start()
        # instead of containers.run(network=...). This ensures the container is on
        # smsly-net from the very first moment Traefik inspects it, avoiding the
        # race condition where Traefik sees the container before net.connect() runs.
        networking_config = self.docker_client.api.create_networking_config({
            network_name: self.docker_client.api.create_endpoint_config(
                aliases=[name, f"{name}.{project_id}.internal"]
            )
        })

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

        # Cleanup existing
        try:
            container = self.docker_client.containers.get(name)
            container.remove(force=True)
        except docker.errors.NotFound:
            pass

        # Traefik Labels — support primary domain + custom domains
        domain = env.get('PUBLIC_DOMAIN', f"{name}.localhost")
        port = env.get('PORT', '8000')

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

        labels = {
            'managed_by': 'smsly-hosting',
            'traefik.enable': 'true',
            f'traefik.http.services.{name}.loadbalancer.server.port': port
        }

        if use_ssl:
            labels.update({
                # HTTP router (redirects to HTTPS)
                f'traefik.http.routers.{name}-http.rule': host_rule,
                f'traefik.http.routers.{name}-http.entrypoints': 'web',
                f'traefik.http.routers.{name}-http.middlewares': f'{name}-redirect',
                f'traefik.http.middlewares.{name}-redirect.redirectscheme.scheme': 'https',
                f'traefik.http.middlewares.{name}-redirect.redirectscheme.permanent': 'true',
                # HTTPS router (main)
                f'traefik.http.routers.{name}.rule': host_rule,
                f'traefik.http.routers.{name}.entrypoints': 'websecure',
                f'traefik.http.routers.{name}.tls': 'true',
                f'traefik.http.routers.{name}.tls.certresolver': 'letsencrypt',
            })
        else:
            # IP Mode / No SSL: simple HTTP router
            labels.update({
                f'traefik.http.routers.{name}.rule': host_rule,
                f'traefik.http.routers.{name}.entrypoints': 'web',
            })

        # Docker-native healthcheck — CRITICAL for Traefik v3
        # Traefik v3 filters containers that are unhealthy or still starting.
        # We ALWAYS set a healthcheck so containers reach "healthy" quickly.
        # IMPORTANT: Use universal commands (wget/curl/shell) — NOT python3.
        # Python3 is not available in Node.js, Go, Rust, or static containers.
        hc_port = env.get('PORT', '8000')
        if healthcheck and healthcheck.get('path'):
            hc_path = healthcheck['path']
            hc_interval = healthcheck.get('interval', 10)
            hc_timeout = healthcheck.get('timeout', 5)
            hc_retries = healthcheck.get('retries', 3)
            # HTTP check: try wget (Alpine/Debian), then curl, then TCP fallback
            hc_cmd = (
                f"wget -q -O /dev/null http://localhost:{hc_port}{hc_path} 2>/dev/null "
                f"|| curl -sf http://localhost:{hc_port}{hc_path} >/dev/null 2>&1 "
                f"|| (echo >/dev/tcp/localhost/{hc_port}) 2>/dev/null "
                f"|| exit 1"
            )
        else:
            hc_interval = 10
            hc_timeout = 3
            hc_retries = 3
            # TCP-only check: works in any container with bash or BusyBox
            hc_cmd = (
                f"wget -q -O /dev/null http://localhost:{hc_port}/ 2>/dev/null "
                f"|| curl -sf http://localhost:{hc_port}/ >/dev/null 2>&1 "
                f"|| (echo >/dev/tcp/localhost/{hc_port}) 2>/dev/null "
                f"|| exit 1"
            )
        docker_healthcheck = docker.types.Healthcheck(
            test=["CMD-SHELL", hc_cmd],
            interval=hc_interval * 1_000_000_000,
            timeout=hc_timeout * 1_000_000_000,
            retries=hc_retries,
            start_period=30 * 1_000_000_000,  # 30s grace — apps need time to boot
        )

        # Resource limits
        run_kwargs = {}
        if memory and memory > 0:
            run_kwargs['mem_limit'] = f"{memory}m"
        if cpu and cpu > 0:
            # cpu is in millicores (e.g. 1024 = 1 vCPU)
            # Docker: cpu_period=100000, cpu_quota = (cpu/1000) * 100000
            run_kwargs['cpu_period'] = 100000
            run_kwargs['cpu_quota'] = int((cpu / 1000) * 100000)

        # Restart policy: on-failure with max 5 retries to prevent infinite loops.
        # "unless-stopped" retries forever; "on-failure" stops after MaximumRetryCount.
        if restart_policy == 'no':
            rp = None
        elif restart_policy == 'unless-stopped':
            rp = {"Name": "on-failure", "MaximumRetryCount": 5}
        else:
            rp = {"Name": restart_policy, "MaximumRetryCount": 5}

        # Use create() + start() so networking_config is applied at creation time.
        # This guarantees the container is on smsly-net before Traefik inspects it.
        container = self.docker_client.containers.create(
            image,
            name=name,
            environment=env,
            network=network_name,
            networking_config=networking_config,
            labels=labels,
            volumes=docker_volumes if docker_volumes else None,
            healthcheck=docker_healthcheck,
            restart_policy=rp,
            **run_kwargs
        )
        container.start()

        return container.id

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
