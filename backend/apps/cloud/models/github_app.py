"""
GitHub App installation model.

Tracks GitHub App installations linked to platform users/organizations.
Each installation represents an approved connection between the Grid
GitHub App and a GitHub user or organization account, with access to specific
repositories.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class GitHubAppInstallation(models.Model):
    """A GitHub App installation linked to a platform user or organization."""

    class AccountType(models.TextChoices):
        USER = "User", "User"
        ORGANIZATION = "Organization", "Organization"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        DELETED = "deleted", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # GitHub-side identifiers
    installation_id = models.BigIntegerField(unique=True)
    account_login = models.CharField(max_length=255)
    account_id = models.BigIntegerField()
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    account_avatar_url = models.URLField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE
    )

    # Platform link — installation can be linked to a user or organization
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="github_app_installations",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="github_app_installations",
    )

    # Repository tracking (synced from GitHub webhook events)
    repository_selection = models.CharField(max_length=20, default="selected")
    repositories = models.JSONField(default=list, blank=True)
    # Each entry: {"id": <int>, "name": "<owner>/<repo>"}

    # Metadata from GitHub
    permissions = models.JSONField(default=dict, blank=True)
    events = models.JSONField(default=list, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'deployments_githubappinstallation'
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["account_login"]),
        ]

    def __str__(self) -> str:
        return f"Installation {self.installation_id} ({self.account_login})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def repo_names(self) -> list[str]:
        """Return list of repo full_names accessible to this installation."""
        return [r["name"] for r in (self.repositories or []) if "name" in r]

    def covers_repo(self, repo_full_name: str) -> bool:
        """Check if this installation covers a given repository."""
        if not repo_full_name or "/" not in repo_full_name:
            return False
        if self.repository_selection == "all":
            return self.account_login == repo_full_name.split("/")[0]
        return repo_full_name in self.repo_names
