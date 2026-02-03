"""Factory module."""
import json
from ..models import CloudProvider
from ..adapters.aws import AWSAdapter
from ..adapters.azure import AzureAdapter
from ..adapters.gcp import GCPAdapter
from ..adapters.local import LocalAdapter
from ..adapters.base import BaseCloudAdapter


def get_cloud_adapter(provider: CloudProvider) -> BaseCloudAdapter:
    """
    Factory function to instantiate the correct adapter for a given CloudProvider.
    """
    if provider.provider_type == CloudProvider.ProviderType.AWS:
        return AWSAdapter(
            access_key=provider.api_key,
            secret_key=provider.api_secret,
            region=provider.region
        )

    elif provider.provider_type == CloudProvider.ProviderType.AZURE:
        return AzureAdapter(
            tenant_id=provider.tenant_id,
            client_id=provider.api_key,
            client_secret=provider.api_secret,
            subscription_id=provider.project_id
        )

    elif provider.provider_type == CloudProvider.ProviderType.GCP:
        service_account_info = json.loads(provider.api_key)
        return GCPAdapter(
            service_account_json=service_account_info,
            project_id=provider.project_id,
            region=provider.region
        )

    elif provider.provider_type == CloudProvider.ProviderType.LOCAL:
        return LocalAdapter()

    raise NotImplementedError(
        f"Provider type {
            provider.provider_type} is not supported.")
