"""Models module."""
import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


class CloudProvider(models.Model):
    class ProviderType(models.TextChoices):
        AWS = 'AWS', 'Amazon Web Services'
        GCP = 'GCP', 'Google Cloud Platform'
        AZURE = 'AZURE', 'Microsoft Azure'
        HETZNER = 'HETZNER', 'Hetzner Cloud'
        UPCLOUD = 'UPCLOUD', 'UpCloud'
        DIGITALOCEAN = 'DIGITALOCEAN', 'DigitalOcean'
        NETCUP = 'NETCUP', 'Netcup'
        RAILWAY = 'RAILWAY', 'Railway'
        VERCEL = 'VERCEL', 'Vercel'
        LOCAL = 'LOCAL', 'Local / K3s'
        REMOTE = 'REMOTE', 'Remote Node'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    name = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        help_text="User-friendly name for this integration")
    provider_type = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=ProviderType.choices)

    # Credentials (Encrypted)
    api_key = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="AWS Access Key / GCP Service Account Key (JSON)")
    api_secret = EncryptedCharField(
        max_length=2048,
        blank=True,
        null=True,
        help_text="AWS Secret Key / Private Key")

    # Specific Configs
    project_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="GCP/Azure Project/Subscription ID")
    tenant_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        blank=True,
        null=True,
        help_text="Azure Tenant ID")
    region = models.CharField(  # type: ignore[var-annotated]
        max_length=50,
        default='us-east-1',
        help_text="Default region for resources")

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.name} ({self.provider_type})"


class CloudResource(models.Model):
    """
    Represents a provisioned resource on a cloud provider.
    Examples: S3 Bucket, RDS Instance, VPC, Azure Resource Group.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    provider = models.ForeignKey(  # type: ignore[var-annotated]
        CloudProvider,
        on_delete=models.CASCADE,
        related_name='resources')

    resource_id = models.CharField(  # type: ignore[var-annotated]
        max_length=255,
        help_text="Provider-specific ID (ARN, SelfLink, etc)")
    resource_type = models.CharField(  # type: ignore[var-annotated]
        max_length=100,
        help_text="e.g., AWS::S3::Bucket, Microsoft.Web/sites")
    name = models.CharField(max_length=255)  # type: ignore[var-annotated]

    region = models.CharField(max_length=50, blank=True)  # type: ignore[var-annotated]
    status = models.CharField(max_length=50, default='PROVISIONING')  # type: ignore[var-annotated]

    metadata = models.JSONField(  # type: ignore[var-annotated]
        default=dict,
        blank=True,
        help_text="Additional properties (tags, specific config)")

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    def __str__(self):
        return f"{self.name} ({self.resource_type})"


class IAMRole(models.Model):
    """
    Represents an IAM Role or Service Account.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    provider = models.ForeignKey(  # type: ignore[var-annotated]
        CloudProvider,
        on_delete=models.CASCADE,
        related_name='iam_roles')
    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    arn = models.CharField(  # type: ignore[var-annotated]
        max_length=512,
        blank=True,
        help_text="Amazon Resource Name or equivalent")
    policy_document = models.JSONField(  # type: ignore[var-annotated]
        default=dict, help_text="IAM Policy JSON")

    def __str__(self):
        return self.name


class Secret(models.Model):
    """
    Represents a stored secret in AWS Secrets Manager / Azure Key Vault / GCP Secret Manager.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    provider = models.ForeignKey(  # type: ignore[var-annotated]
        CloudProvider,
        on_delete=models.CASCADE,
        related_name='secrets')
    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    arn = models.CharField(max_length=512, blank=True)  # type: ignore[var-annotated]
    version_id = models.CharField(max_length=255, blank=True)  # type: ignore[var-annotated]

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    def __str__(self):
        return self.name
