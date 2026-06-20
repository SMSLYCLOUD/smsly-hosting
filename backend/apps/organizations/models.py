"""Organizations — multi-tenant top-level entity with SSO support.

Hierarchy:
  Organization → Teams → Projects → Services

An Organization owns teams, manages members at the org level,
and can have SSO providers configured for automatic login.
"""
import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedTextField


class Organization(models.Model):
    """Top-level tenant. Contains teams, members, and SSO configuration."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=128, unique=True, help_text="URL-safe identifier")
    owner = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE,
        related_name='owned_organizations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    """Membership and role within an organization."""
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Owner'
        ADMIN = 'ADMIN', 'Admin'
        MEMBER = 'MEMBER', 'Member'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE,
        related_name='memberships',
    )
    user = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE,
        related_name='organization_memberships',
    )
    role = models.CharField(
        max_length=20, choices=Role.choices, default=Role.MEMBER,
    )
    invited_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('organization', 'user')]
        verbose_name = "Organization Membership"
        verbose_name_plural = "Organization Memberships"

    def __str__(self):
        return f"{self.user} ({self.role}) @ {self.organization}"


class OrganizationSSO(models.Model):
    """SSO provider configuration for an organization.

    Supports SAML2 and OIDC providers. The platform acts as the SP
    (Service Provider); the org's IdP authenticates users and maps
    them to OrganizationMembership based on email domain or attribute.
    """
    class ProviderType(models.TextChoices):
        SAML = 'SAML', 'SAML 2.0'
        OIDC = 'OIDC', 'OpenID Connect'
        GOOGLE_WORKSPACE = 'GOOGLE_WORKSPACE', 'Google Workspace'
        AZURE_AD = 'AZURE_AD', 'Azure AD / Entra ID'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE,
        related_name='sso_providers',
    )
    provider_type = models.CharField(
        max_length=32, choices=ProviderType.choices,
    )
    label = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Display name (e.g. 'Company Okta')",
    )
    is_active = models.BooleanField(default=True)

    # ── OIDC fields ──
    oidc_issuer_url = models.URLField(blank=True, default='')
    oidc_client_id = EncryptedTextField(blank=True, default='')
    oidc_client_secret = EncryptedTextField(blank=True, default='')

    # ── SAML fields ──
    saml_entity_id = models.URLField(blank=True, default='')
    saml_sso_url = models.URLField(blank=True, default='')
    saml_x509_cert = EncryptedTextField(blank=True, default='')

    # ── Domain auto-provisioning ──
    auto_provision_domains = models.JSONField(
        default=list, blank=True,
        help_text="Email domains that auto-provision users (e.g. ['company.com'])",
    )
    default_role = models.CharField(
        max_length=20, choices=OrganizationMembership.Role.choices,
        default=OrganizationMembership.Role.MEMBER,
        help_text="Role assigned to auto-provisioned users",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Organization SSO Provider"
        verbose_name_plural = "Organization SSO Providers"

    def __str__(self):
        return f"{self.get_provider_type_display()} - {self.organization.name}"
