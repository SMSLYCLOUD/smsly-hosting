"""Organizations — multi-tenant top-level entity with SSO support.

Hierarchy:
  Organization -> Teams -> Projects -> Services

An Organization owns teams, manages members at the org level,
and can have SSO providers configured for automatic login.
"""
import uuid

from django.db import models


class Organization(models.Model):
    """Top-level tenant. Contains teams, members, and SSO configuration."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    name = models.CharField(max_length=255)  # type: ignore[var-annotated]
    slug = models.SlugField(max_length=128, unique=True, help_text="URL-safe identifier")  # type: ignore[var-annotated]
    owner = models.ForeignKey(  # type: ignore[var-annotated]
        'auth.User', on_delete=models.CASCADE,
        related_name='owned_organizations',
    )
    created_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    updated_at = models.DateTimeField(auto_now=True)  # type: ignore[var-annotated]
    is_active = models.BooleanField(default=True)  # type: ignore[var-annotated]

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # type: ignore[var-annotated]
    organization = models.ForeignKey(  # type: ignore[var-annotated]
        Organization, on_delete=models.CASCADE,
        related_name='memberships',
    )
    user = models.ForeignKey(  # type: ignore[var-annotated]
        'auth.User', on_delete=models.CASCADE,
        related_name='organization_memberships',
    )
    role = models.CharField(  # type: ignore[var-annotated]
        max_length=20, choices=Role.choices, default=Role.MEMBER,
    )
    can_manage_billing = models.BooleanField(  # type: ignore[var-annotated]
        default=False,
        help_text="Allow this member to manage billing (plans, checkout, invoices)",
    )
    is_active = models.BooleanField(  # type: ignore[var-annotated]
        default=True,
        help_text="Suspend membership without removing the record",
    )
    expires_at = models.DateTimeField(  # type: ignore[var-annotated]
        null=True, blank=True,
        help_text="If set, membership automatically expires after this date",
    )
    invited_at = models.DateTimeField(auto_now_add=True)  # type: ignore[var-annotated]
    accepted_at = models.DateTimeField(null=True, blank=True)  # type: ignore[var-annotated]

    class Meta:
        unique_together = [('organization', 'user')]
        verbose_name = "Organization Membership"
        verbose_name_plural = "Organization Memberships"

    def __str__(self):
        return f"{self.user} ({self.role}) @ {self.organization}"
