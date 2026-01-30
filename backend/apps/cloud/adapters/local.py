import docker
import logging
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

        try:
            self.docker_client = docker.from_env()
        except Exception as e:
            logger.warning(f"Docker client not available: {e}")

        try:
            try:
                config.load_incluster_config()
            except:
                config.load_kube_config()
            self.k8s_client = client.CoreV1Api()
            self.k8s_apps = client.AppsV1Api()
        except Exception as e:
            logger.warning(f"Kubernetes client not available: {e}")

    def authenticate(self) -> bool:
        return self.docker_client is not None or self.k8s_client is not None

    def deploy_container(self, service_name: str, image: str, env_vars: Dict[str, str], cpu: int, memory: int) -> str:
        if self.k8s_client:
            return self._deploy_k8s(service_name, image, env_vars, cpu, memory)
        elif self.docker_client:
            return self._deploy_docker(service_name, image, env_vars)
        else:
            raise RuntimeError("No local orchestrator available")

    def _deploy_docker(self, name: str, image: str, env: Dict[str, str]) -> str:
        # Ensure shared network exists
        network_name = 'smsly-net'
        try:
            self.docker_client.networks.get(network_name)
        except docker.errors.NotFound:
            self.docker_client.networks.create(network_name, driver="bridge")

        # Cleanup existing
        try:
            container = self.docker_client.containers.get(name)
            container.remove(force=True)
        except docker.errors.NotFound:
            pass

        # Prepare Traefik Labels for Ingress
        # Defaulting to .localhost for dev/local use. In prod, env var would set base domain.
        domain = env.get('PUBLIC_DOMAIN', f"{name}.localhost")
        port = env.get('PORT', '8000')

        labels = {
            'managed_by': 'smsly-hosting',
            'traefik.enable': 'true',
            f'traefik.http.routers.{name}.rule': f'Host(`{domain}`)',
            f'traefik.http.routers.{name}.entrypoints': 'web',
            f'traefik.http.services.{name}.loadbalancer.server.port': port
        }

        container = self.docker_client.containers.run(
            image,
            name=name,
            environment=env,
            detach=True,
            network=network_name,
            labels=labels
        )
        return container.id

    def _deploy_k8s(self, name: str, image: str, env: Dict[str, str], cpu: int, memory: int) -> str:
        namespace = 'default'

        # 1. Deployment
        deployment = client.V1Deployment(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1DeploymentSpec(
                replicas=1,
                selector=client.V1LabelSelector(match_labels={"app": name}),
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"app": name}),
                    spec=client.V1PodSpec(
                        containers=[
                            client.V1Container(
                                name=name,
                                image=image,
                                env=[client.V1EnvVar(name=k, value=v) for k, v in env.items()],
                                resources=client.V1ResourceRequirements(
                                    requests={"cpu": f"{cpu}m", "memory": f"{memory}Mi"},
                                    limits={"cpu": f"{cpu*2}m", "memory": f"{memory*2}Mi"}
                                )
                            )
                        ]
                    )
                )
            )
        )

        try:
            self.k8s_apps.create_namespaced_deployment(namespace=namespace, body=deployment)
        except client.exceptions.ApiException as e:
            if e.status == 409:
                self.k8s_apps.patch_namespaced_deployment(name=name, namespace=namespace, body=deployment)
            else:
                raise

        # 2. Service (ClusterIP) for Discovery
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(name=name),
            spec=client.V1ServiceSpec(
                selector={"app": name},
                ports=[client.V1ServicePort(port=80, target_port=int(env.get('PORT', 8000)))],
                type="ClusterIP"
            )
        )

        try:
            self.k8s_client.create_namespaced_service(namespace=namespace, body=svc)
        except client.exceptions.ApiException as e:
            if e.status != 409:
                logger.warning(f"Failed to create service for {name}: {e}")

        return f"k8s://{namespace}/{name}"

    # --- Other Methods ---
    def deploy_function(self, function_name: str, code_zip: str, handler: str, runtime: str) -> str:
        raise NotImplementedError("Local Functions not yet supported")

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        return f"local://{bucket_name}"

    def provision_database(self, db_name: str, engine: str, version: str) -> str:
        if self.docker_client:
             network_name = 'smsly-net'
             try:
                self.docker_client.networks.get(network_name)
             except docker.errors.NotFound:
                self.docker_client.networks.create(network_name, driver="bridge")

             container = self.docker_client.containers.run(
                f"{engine}:{version}-alpine",
                name=f"db-{db_name}",
                environment={"POSTGRES_PASSWORD": "password"},
                detach=True,
                network=network_name
            )
             return container.id
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

    def get_metrics(self, resource_id: str, metric_name: str, start_time: str, end_time: str) -> List[Dict]:
        return []
