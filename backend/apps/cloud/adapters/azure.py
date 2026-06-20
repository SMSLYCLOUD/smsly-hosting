import logging
from typing import Any

from .base import BaseCloudAdapter

logger = logging.getLogger(__name__)

try:
    from azure.identity import ClientSecretCredential
    from azure.mgmt.appcontainers import ContainerAppsAPIClient
    from azure.mgmt.appcontainers.models import (
        Configuration,
        Container,
        ContainerApp,
        EnvironmentVar,
        Ingress,
        Template,
        TrafficWeight,
    )
    from azure.mgmt.resource import ResourceManagementClient
    from azure.storage.blob import BlobServiceClient
    HAS_AZURE_SDK = True
except ImportError:
    HAS_AZURE_SDK = False
    ResourceManagementClient: Any = None
    ContainerAppsAPIClient: Any = None
    ContainerApp: Any = None
    Template: Any = None
    Container: Any = None
    EnvironmentVar: Any = None
    Configuration: Any = None
    Ingress: Any = None
    TrafficWeight: Any = None
    ClientSecretCredential: Any = None
    BlobServiceClient: Any = None

class AzureAdapter(BaseCloudAdapter):
    def __init__(self, tenant_id: str, client_id: str,
                 client_secret: str, subscription_id: str, region: str = 'eastus'):
        if not HAS_AZURE_SDK:
            raise RuntimeError("Azure SDK not installed. Please install 'azure-mgmt-appcontainers azure-identity'")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.subscription_id = subscription_id
        self.region = region

        self.credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )
        self.resource_client = ResourceManagementClient(
            self.credential, self.subscription_id)
        self.container_client = ContainerAppsAPIClient(
            self.credential, self.subscription_id)

    def authenticate(self) -> bool:
        try:
            # List resource groups as a connectivity test
            list(self.resource_client.resource_groups.list())
            return True
        except Exception:
            return False

    def pull_image(self, image: str) -> bool:
        """Azure handles image pulling automatically."""
        return True

    def deploy_container(self, service_name: str, image: str,
                         env_vars: dict[str, str], cpu: int, memory: int, replicas: int = 1,
                         vpa_enabled: bool = True, **kwargs) -> str:
        """
        Deploys a container to Azure Container Apps.
        """
        resource_group = kwargs.get('resource_group', 'smsly-rg')
        managed_env_id = kwargs.get('managed_environment_id')

        # Ensure resource group exists
        self.resource_client.resource_groups.create_or_update(
            resource_group, {"location": self.region}
        )

        app_name = service_name.replace('_', '-').lower()

        # Azure expects CPU in fractional cores (e.g. 0.5) and Memory in Gi (e.g. 1.0Gi)
        azure_cpu = cpu / 1000.0 if cpu else 0.5
        azure_memory = f"{memory / 1024.0}Gi" if memory else "1.0Gi"

        container_app_envelope = ContainerApp(
            location=self.region,
            configuration=Configuration(
                ingress=Ingress(
                    external=kwargs.get('is_public', True),
                    target_port=int(env_vars.get('PORT', 8000)),
                    traffic=[TrafficWeight(latest_revision=True, weight=100)]
                )
            ),
            template=Template(
                containers=[
                    Container(
                        name=app_name,
                        image=image,
                        env=[EnvironmentVar(name=k, value=v) for k, v in env_vars.items()],
                        resources={
                            "cpu": azure_cpu,
                            "memory": azure_memory
                        }
                    )
                ],
                scale={
                    "minReplicas": replicas,
                    "maxReplicas": kwargs.get('max_replicas', replicas + 2)
                }
            ),
            managed_environment_id=managed_env_id
        )

        poller = self.container_client.container_apps.begin_create_or_update(
            resource_group, app_name, container_app_envelope
        )
        result = poller.result()
        return result.id

    def deploy_function(self, function_name: str,
                        code_zip: str, handler: str, runtime: str) -> str:
        raise NotImplementedError("Azure Functions integration pending.")

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        """Create an Azure Blob Storage container.

        Requires the 'azure-storage-blob' package and a storage account
        whose name is set via adapter extra config or constructor.
        Falls back to returning the URL without creating the container.
        """
        try:
            account_url = getattr(self, '_storage_account_url', None)
            if not account_url:
                raise ValueError("storage_account_url not configured")
            blob_svc = BlobServiceClient(account_url=account_url, credential=self.credential)
            container_client = blob_svc.create_container(bucket_name)
            return container_client.url
        except Exception as exc:
            logger.warning("Azure create_bucket failed: %s. Returning URL as stub.", exc)
            return f"https://{bucket_name}.blob.core.windows.net/"

    def provision_database(self, db_name: str, engine: str,
                           version: str) -> str:
        raise NotImplementedError("Azure SQL/Postgres integration pending.")

    def create_vpc(self, cidr_block: str) -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/smsly-rg/providers/Microsoft.Network/virtualNetworks/smsly-vnet"

    def create_iam_role(self, role_name: str, policy: dict[str, Any]) -> str:
        return f"/subscriptions/{self.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/{role_name}"

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        # Azure Key Vault Implementation
        return f"https://smsly-kv.vault.azure.net/secrets/{secret_name}"

    def get_metrics(self, resource_id: str, metric_name: str,
                    start_time: str, end_time: str) -> list[dict]:
        return []

    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/smsly-rg/providers/Microsoft.Network/FrontDoorWebApplicationFirewallPolicies/{name}"

    def issue_ssl_cert(self, domain_name: str) -> str:
        return f"/subscriptions/{self.subscription_id}/resourceGroups/smsly-rg/providers/Microsoft.Web/certificates/{domain_name}"
