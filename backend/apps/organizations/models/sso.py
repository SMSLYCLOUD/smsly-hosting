import uuid

from django.db import models
from encrypted_model_fields.fields import EncryptedTextField

from .org import OrganizationMembership


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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    organization = models.ForeignKey(  # type: ignore[var-annotated]
        'organizations.Organization', on_delete=models.CASCADE,
        related_name='sso_providers',
    )
    provider_type = models.CharField(  # type: ignore[var-annotated]
        max_length=32, choices=ProviderType.choices,
    )
    label = models.CharField(  # type: ignore[var-annotated]
        max_length=255, blank=True, default='',
        help_text="Display name (e.g. 'Company Okta')",
    )
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]

    # -- OIDC fields --
    oidc_issuer_url = models.URLField(blank=True, default='')  # type: ignore[var-annotated]
    oidc_client_id = EncryptedTextField(blank=True, default='')
    oidc_client_secret = EncryptedTextField(blank=True, default='')

    # -- SAML fields --
    saml_entity_id = models.URLField(blank=True, default='')  # type: ignore[var-annotated]
    saml_sso_url = models.URLField(blank=True, default='')  # type: ignore[var-annotated]
    saml_x509_cert = EncryptedTextField(blank=True, default='')

    # -- Domain auto-provisioning --
    auto_provision_domains = models.JSONField(  # type: ignore[var-annotated]
        default=list, blank=True,
        help_text="Email domains that auto-provision users (e.g. ['company.com'])",
    )
    default_role = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=OrganizationMembership.Role.choices,
        default=OrganizationMembership.Role.MEMBER,
        help_text="Role assigned to auto-provisioned users",
    )

    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]

    class Meta:
        verbose_name = "Organization SSO Provider"
        verbose_name_plural = "Organization SSO Providers"

    def __str__(self):
        return f"{self.get_provider_type_display()} - {self.organization.name}"
