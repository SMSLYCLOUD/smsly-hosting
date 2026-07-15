"""Django admin configuration for deployments app."""
from django.contrib import admin

from .models_github_app import GitHubAppInstallation


@admin.register(GitHubAppInstallation)
class GitHubAppInstallationAdmin(admin.ModelAdmin):
    list_display = [
        "installation_id",
        "account_login",
        "account_type",
        "status",
        "user",
        "created_at",
    ]
    list_filter = ["status", "account_type"]
    search_fields = ["account_login", "installation_id"]
    readonly_fields = [
        "id",
        "installation_id",
        "account_id",
        "created_at",
        "updated_at",
    ]
