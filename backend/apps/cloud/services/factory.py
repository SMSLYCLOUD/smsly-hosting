"""Factory module."""
import json

from ..adapters.aws import AWSAdapter
from ..adapters.azure import AzureAdapter
from ..adapters.base import BaseCloudAdapter
from ..adapters.digitalocean import DigitalOceanAdapter
from ..adapters.gcp import GCPAdapter
from ..adapters.hetzner import HetznerAdapter
from ..adapters.local import LocalAdapter
from ..adapters.netcup import NetcupAdapter
from ..adapters.upcloud import UpCloudAdapter
from ..models import CloudProvider


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

    elif provider.provider_type == CloudProvider.ProviderType.HETZNER:
        return HetznerAdapter(api_token=provider.api_secret or provider.api_key)

    elif provider.provider_type == CloudProvider.ProviderType.UPCLOUD:
        return UpCloudAdapter(username=provider.api_key or "", password=provider.api_secret or "")

    elif provider.provider_type == CloudProvider.ProviderType.DIGITALOCEAN:
        return DigitalOceanAdapter(api_token=provider.api_secret or provider.api_key or "")

    elif provider.provider_type == CloudProvider.ProviderType.NETCUP:
        return NetcupAdapter(
            api_key=provider.api_key or "",
            api_password=provider.api_secret or "",
            customer_number=provider.project_id or "",
        )

    elif provider.provider_type in (CloudProvider.ProviderType.RAILWAY, CloudProvider.ProviderType.VERCEL):
        raise NotImplementedError(
            f"Provider {provider.provider_type} is managed externally — no adapter needed.")

    elif provider.provider_type == CloudProvider.ProviderType.REMOTE:
        return LocalAdapter()

    raise NotImplementedError(
        f"Provider type {provider.provider_type} is not supported.")
