"""Compute module."""
from ..models import CloudProvider, CloudResource
from ..adapters.aws import AWSAdapter
from ..adapters.azure import AzureAdapter
from ..adapters.gcp import GCPAdapter
from ..adapters.local import LocalAdapter
from typing import Dict, Optional


class ComputeService:
    def __init__(self, provider: CloudProvider):
        self.provider = provider
        self.adapter = self._get_adapter()

    def _get_adapter(self):
        if self.provider.provider_type == CloudProvider.ProviderType.AWS:
            return AWSAdapter(
                access_key=self.provider.api_key,
                secret_key=self.provider.api_secret,
                region=self.provider.region
            )
        elif self.provider.provider_type == CloudProvider.ProviderType.AZURE:
            return AzureAdapter(
                tenant_id=self.provider.tenant_id,
                client_id=self.provider.api_key,
                client_secret=self.provider.api_secret,
                subscription_id=self.provider.project_id
            )
        elif self.provider.provider_type == CloudProvider.ProviderType.GCP:
            # Assuming api_key stores the service account JSON string
            import json
            service_account_info = json.loads(self.provider.api_key)
            return GCPAdapter(
                service_account_json=service_account_info,
                project_id=self.provider.project_id,
                region=self.provider.region
            )
        elif self.provider.provider_type == CloudProvider.ProviderType.LOCAL:
            return LocalAdapter()
        else:
            raise NotImplementedError(
                f"Provider {self.provider.provider_type} not supported yet")

    def pull_image(self, image: str) -> bool:
        """
        Pull a container image using the provider's adapter.
        """
        return self.adapter.pull_image(image)

    # pylint: disable=too-many-positional-arguments

        # pylint: disable=too-many-positional-arguments
    def deploy_container(self, name: str, image: str,
                         env_vars: Dict[str, str], cpu: int = 1000, memory: int = 2048,
                         replicas: int = 1, vpa_enabled: bool = False, **kwargs) -> CloudResource:
        """
        Deploy a container service (ECS/Cloud Run/Azure Container Apps).
        """
        resource_id = self.adapter.deploy_container(
            name, image, env_vars, cpu, memory, replicas, vpa_enabled=vpa_enabled, **kwargs)

        resource, created = CloudResource.objects.update_or_create(
            provider=self.provider,
            resource_id=resource_id,
            defaults={
                'name': name,
                'resource_type': 'CONTAINER_SERVICE',
                'region': self.provider.region,
                'status': 'ACTIVE'
            }
        )
        return resource

    def deploy_function(self, name: str, code_zip: bytes,
                        handler: str, runtime: str) -> CloudResource:
        """
        Deploy a serverless function (Lambda/Cloud Functions/Azure Functions).
        """
        resource_id = self.adapter.deploy_function(
            name, code_zip, handler, runtime)

        resource, created = CloudResource.objects.update_or_create(
            provider=self.provider,
            resource_id=resource_id,
            defaults={
                'name': name,
                'resource_type': 'SERVERLESS_FUNCTION',
                'region': self.provider.region,
                'status': 'ACTIVE'
            }
        )
        return resource

    def deploy_batch_job(self, name: str, command: str,
                         image: str) -> CloudResource:
        """
        Submit a batch job (AWS Batch / Azure Batch).
        """
        deploy_batch = getattr(self.adapter, "deploy_batch_job", None)
        if not callable(deploy_batch):
            raise NotImplementedError(
                f"Batch job deployment is not implemented for provider "
                f"{self.provider.provider_type}."
            )

        resource_id = deploy_batch(name=name, command=command, image=image)
        resource, _created = CloudResource.objects.update_or_create(
            provider=self.provider,
            resource_id=resource_id,
            defaults={
                'name': name,
                'resource_type': 'BATCH_JOB',
                'region': self.provider.region,
                'status': 'SUBMITTED',
                'metadata': {
                    'command': command,
                    'image': image,
                },
            },
        )
        return resource
