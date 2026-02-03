"""Data module."""
from ..models import CloudProvider, CloudResource
from .factory import get_cloud_adapter
from typing import Optional, Dict


class DataService:
    def __init__(self, provider: CloudProvider):
        self.provider = provider
        self.adapter = get_cloud_adapter(provider)

    def provision_database(self, name: str, engine: str,
                           version: str) -> CloudResource:
        """
        Provision a managed database (RDS, Cloud SQL, Azure SQL).
        Engine: 'postgres', 'mysql', 'mssql', 'redis', 'mongodb'
        """
        # Call the underlying adapter to create the DB
        resource_id = self.adapter.provision_database(name, engine, version)

        # Create resource record
        resource, created = CloudResource.objects.update_or_create(
            provider=self.provider,
            resource_id=resource_id,
            defaults={
                'name': name,
                'resource_type': f"MANAGED_DB::{engine.upper()}",
                'region': self.provider.region,
                'status': 'PROVISIONING',
                'metadata': {'engine': engine, 'version': version}
            }
        )
        return resource


class StorageService:
    def __init__(self, provider: CloudProvider):
        self.provider = provider
        self.adapter = get_cloud_adapter(provider)

    def create_bucket(self, bucket_name: str,
                      public: bool = False) -> CloudResource:
        """
        Create an Object Storage bucket (S3, GCS, Azure Blob).
        """
        resource_id = self.adapter.create_bucket(bucket_name, public)

        resource, created = CloudResource.objects.update_or_create(
            provider=self.provider,
            resource_id=resource_id,
            defaults={
                'name': bucket_name,
                'resource_type': 'OBJECT_STORAGE',
                'region': self.provider.region,
                'status': 'ACTIVE',
                'metadata': {'public': public}
            }
        )
        return resource
