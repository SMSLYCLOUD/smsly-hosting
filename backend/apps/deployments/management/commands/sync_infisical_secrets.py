"""
Management command to sync secrets between PlatformConfig and Infisical.

Usage:
    python manage.py sync_infisical_secrets          # Push PlatformConfig → Infisical
    python manage.py sync_infisical_secrets --pull    # Pull Infisical → PlatformConfig
    python manage.py sync_infisical_secrets --push --workspace my-workspace
"""

from django.core.management.base import BaseCommand

from ...services.infisical import (
    get_infisical_client,
    get_or_create_workspace,
    pull_platform_config_from_infisical,
    push_platform_config_to_infisical,
)


class Command(BaseCommand):
    help = "Sync secrets between PlatformConfig and Infisical."

    def add_arguments(self, parser):
        parser.add_argument("--push", action="store_true", help="Push PlatformConfig secrets to Infisical (default)")
        parser.add_argument("--pull", action="store_true", help="Pull secrets from Infisical into PlatformConfig")
        parser.add_argument("--workspace", type=str, default="smsly-platform", help="Infisical workspace name")

    def handle(self, *args, **options):
        client = get_infisical_client()
        if client is None:
            self.stderr.write("ERROR: Infisical not configured (INFISICAL_SERVICE_TOKEN missing)")
            return

        workspace_id = get_or_create_workspace(client, options["workspace"])
        if workspace_id is None:
            self.stderr.write("ERROR: Could not resolve or create Infisical workspace")
            return

        self.stdout.write(f"Infisical workspace: {options['workspace']} ({workspace_id})")

        if options.get("pull"):
            self.stdout.write("Pulling secrets from Infisical → PlatformConfig...")
            result = pull_platform_config_from_infisical(client, workspace_id)
        else:
            self.stdout.write("Pushing PlatformConfig → Infisical...")
            result = push_platform_config_to_infisical(client, workspace_id)

        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS(
                f"Synced {len(result.get('synced', result.get('updated', [])))} secret(s), "
                f"{len(result.get('skipped', []))} skipped"
            ))
        else:
            error = result.get("error", "Unknown error")
            self.stderr.write(self.style.ERROR(f"Sync failed: {error}"))

        synced = result.get("synced", result.get("updated", []))
        if synced:
            self.stdout.write(f"  Synced: {', '.join(synced)}")
        failed = result.get("failed", [])
        if failed:
            self.stderr.write(f"  Failed: {', '.join(failed)}")
        skipped = result.get("skipped", [])
        if skipped:
            self.stdout.write(f"  Skipped (empty): {', '.join(skipped)}")
