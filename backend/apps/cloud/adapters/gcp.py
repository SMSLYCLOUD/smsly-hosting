from typing import Any

from .base import BaseCloudAdapter

try:
    from google.cloud import run_v2
    from google.iam.v1 import policy_pb2
    from google.oauth2 import service_account
    HAS_GCP_SDK = True
except ImportError:
    HAS_GCP_SDK = False
    run_v2: Any = None  # type: ignore[no-redef]
    policy_pb2: Any = None  # type: ignore[no-redef]
    service_account: Any = None  # type: ignore[no-redef]

class GCPAdapter(BaseCloudAdapter):
    def __init__(self, service_account_json: dict,
                 project_id: str, region: str = 'us-central1'):
        if not HAS_GCP_SDK:
            raise RuntimeError("GCP SDK not installed. Please install 'google-cloud-run google-auth'")
        self.credentials = service_account.Credentials.from_service_account_info(
            service_account_json)
        self.project_id = project_id
        self.region = region
        self.client = run_v2.ServicesClient(credentials=self.credentials)

    def authenticate(self) -> bool:
        try:
            # Simple test call to verify credentials
            list(self.client.list_services(parent=f"projects/{self.project_id}/locations/{self.region}"))
            return True
        except Exception:
            return False

    def pull_image(self, image: str) -> bool:
        """GCP handles image pulling automatically."""
        return True

    def deploy_container(self, service_name: str, image: str,
                         env_vars: dict[str, str], cpu: int, memory: int, replicas: int = 1,
                         vpa_enabled: bool = True, **kwargs) -> str:
        """
        Deploys a container to Google Cloud Run.
        """
        parent = f"projects/{self.project_id}/locations/{self.region}"
        service_id = service_name.replace('_', '-').lower()
        service_path = f"{parent}/services/{service_id}"

        # Resources: GCP uses e.g. "1000m" for 1 CPU, "512Mi" for memory
        # Input cpu is millicores, memory is MB
        gcp_cpu = f"{cpu}m" if cpu else "1000m"
        gcp_memory = f"{memory}Mi" if memory else "512Mi"

        container = run_v2.Container(
            image=image,
            env=[run_v2.EnvVar(name=k, value=v) for k, v in env_vars.items()],
            resources=run_v2.ResourceRequirements(
                limits={"cpu": gcp_cpu, "memory": gcp_memory}
            ),
        )

        service = run_v2.Service(
            template=run_v2.RevisionTemplate(
                containers=[container],
                scaling=run_v2.RevisionScaling(min_instance_count=replicas, max_instance_count=kwargs.get('max_replicas', replicas + 2))
            )
        )

        try:
            # Check if exists
            self.client.get_service(name=service_path)
            operation = self.client.update_service(service=service, name=service_path)
        except Exception:
            # Create new
            operation = self.client.create_service(parent=parent, service=service, service_id=service_id)

        operation.result()  # Wait for completion

        # Set IAM policy to allow unauthenticated access if it's a public service
        if kwargs.get('is_public', True):
            self._allow_unauthenticated_access(service_path)

        return service_path

    def _allow_unauthenticated_access(self, service_path: str):
        """Set IAM policy to allow 'allUsers' to invoke the service."""
        policy = self.client.get_iam_policy(resource=service_path)
        binding = policy_pb2.Binding(
            role="roles/run.invoker",
            members=["allUsers"]
        )
        policy.bindings.append(binding)
        self.client.set_iam_policy(resource=service_path, policy=policy)

    def deploy_function(self, function_name: str,
                        code_zip: str, handler: str, runtime: str) -> str:
        raise NotImplementedError("GCP Cloud Functions integration pending.")

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        from google.cloud import storage
        client = storage.Client(credentials=self.credentials, project=self.project_id)
        bucket = client.create_bucket(bucket_name, location=self.region)
        if public:
            bucket.make_public(recursive=True, future=True)
        return f"gs://{bucket_name}"

    def provision_database(self, db_name: str, engine: str,
                           version: str) -> str:
        raise NotImplementedError("GCP Cloud SQL integration pending.")

    def create_vpc(self, cidr_block: str) -> str:
        return f"projects/{self.project_id}/global/networks/smsly-vpc"

    def create_iam_role(self, role_name: str, policy: dict[str, Any]) -> str:
        return f"projects/{self.project_id}/roles/{role_name}"

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient(credentials=self.credentials)
        parent = f"projects/{self.project_id}"
        try:
            client.create_secret(parent=parent, secret_id=secret_name, secret={'replication': {'automatic': {}}})
        except Exception:
            pass  # Secret already exists

        payload = secret_value.encode("UTF-8")
        client.add_secret_version(parent=f"{parent}/secrets/{secret_name}", payload={'data': payload})
        return f"projects/{self.project_id}/secrets/{secret_name}"

    def get_metrics(self, resource_id: str, metric_name: str,
                    start_time: str, end_time: str) -> list[dict]:
        return []

    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        return f"projects/{self.project_id}/global/securityPolicies/{name}"

    def issue_ssl_cert(self, domain_name: str) -> str:
        return f"projects/{self.project_id}/locations/global/certificates/{domain_name.replace('.', '-')}"
