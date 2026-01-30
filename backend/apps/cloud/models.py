from django.db import models
from encrypted_model_fields.fields import EncryptedCharField
import uuid

class CloudProvider(models.Model):
    class ProviderType(models.TextChoices):
        AWS = 'AWS', 'Amazon Web Services'
        GCP = 'GCP', 'Google Cloud Platform'
        AZURE = 'AZURE', 'Microsoft Azure'
        RAILWAY = 'RAILWAY', 'Railway'
        VERCEL = 'VERCEL', 'Vercel'
        LOCAL = 'LOCAL', 'Local / K3s'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, help_text="User-friendly name for this integration")
    provider_type = models.CharField(max_length=20, choices=ProviderType.choices)

    # Credentials (Encrypted)
    api_key = EncryptedCharField(max_length=255, blank=True, null=True, help_text="AWS Access Key / GCP Service Account Key (JSON)")
    api_secret = EncryptedCharField(max_length=2048, blank=True, null=True, help_text="AWS Secret Key / Private Key")

    # Specific Configs
    project_id = models.CharField(max_length=255, blank=True, null=True, help_text="GCP/Azure Project/Subscription ID")
    tenant_id = models.CharField(max_length=255, blank=True, null=True, help_text="Azure Tenant ID")
    region = models.CharField(max_length=50, default='us-east-1', help_text="Default region for resources")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.provider_type})"

class CloudResource(models.Model):
    """
    Represents a provisioned resource on a cloud provider.
    Examples: S3 Bucket, RDS Instance, VPC, Azure Resource Group.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(CloudProvider, on_delete=models.CASCADE, related_name='resources')

    resource_id = models.CharField(max_length=255, help_text="Provider-specific ID (ARN, SelfLink, etc)")
    resource_type = models.CharField(max_length=100, help_text="e.g., AWS::S3::Bucket, Microsoft.Web/sites")
    name = models.CharField(max_length=255)

    region = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=50, default='PROVISIONING')

    metadata = models.JSONField(default=dict, blank=True, help_text="Additional properties (tags, specific config)")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.resource_type})"

class IAMRole(models.Model):
    """
    Represents an IAM Role or Service Account.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(CloudProvider, on_delete=models.CASCADE, related_name='iam_roles')
    name = models.CharField(max_length=255)
    arn = models.CharField(max_length=512, blank=True, help_text="Amazon Resource Name or equivalent")
    policy_document = models.JSONField(default=dict, help_text="IAM Policy JSON")

    def __str__(self):
        return self.name

class Secret(models.Model):
    """
    Represents a stored secret in AWS Secrets Manager / Azure Key Vault / GCP Secret Manager.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(CloudProvider, on_delete=models.CASCADE, related_name='secrets')
    name = models.CharField(max_length=255)
    arn = models.CharField(max_length=512, blank=True)
    version_id = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
