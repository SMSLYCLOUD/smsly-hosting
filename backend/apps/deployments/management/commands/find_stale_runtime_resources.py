import docker
from django.core.management.base import BaseCommand

from apps.deployments.models.addons import Addon
from apps.deployments.models.core import Service


class Command(BaseCommand):
    help = "Find and clean up stale/duplicate runtime resources that don't match active DB state (legacy pattern based)."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only report, do not delete.')
        parser.add_argument('--apply', action='store_true', help='Delete stale resources.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        apply = options['apply']

        if not dry_run and not apply:
            self.stdout.write(self.style.ERROR("Must specify --dry-run or --apply"))
            return

        try:
            client = docker.from_env()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to connect to Docker: {e}"))
            return

        all_containers = client.containers.list(all=True)

        stale_containers = []

        # Find known active slugs/IDs
        active_service_slugs = set(Service.objects.exclude(status='DELETED').values_list('name', flat=True))
        active_addon_ids = {str(a.id) for a in Addon.objects.exclude(status='DELETED')}

        for c in all_containers:
            c_name = c.name.lower()

            # Identify core platform containers to skip
            if 'smsly-hosting' in c_name or ('postgres' in c_name and 'addon' not in c_name) or ('redis' in c_name and 'addon' not in c_name):
                continue

            # Identify stale addons by legacy pattern: "smsly-addon-postgres-<uuid>"
            if 'smsly-addon-' in c_name:
                parts = c_name.split('-')
                if len(parts) >= 4:
                    # extract potential UUID
                    "-".join(parts[-5:]) if len(parts) >= 8 else "-".join(parts[-1:])
                    # Actually uuid is 36 chars. Let's just substring check against active ids
                    is_active = any(aid in c_name for aid in active_addon_ids)
                    if not is_active:
                        stale_containers.append((c, "Addon not found in active DB"))
                continue

            # Identify old green deployments: "slug-green-xyz"
            if '-green-' in c_name:
                # The slug is everything before "-green-"
                slug = c_name.split('-green-')[0]
                if slug not in active_service_slugs:
                    stale_containers.append((c, f"Orphan green container for slug {slug}"))
                continue

            # Identify orphaned rollback backup containers: "slug-rollback-xyz"
            if '-rollback-' in c_name:
                slug = c_name.split('-rollback-')[0]
                if slug not in active_service_slugs:
                    stale_containers.append((c, f"Orphan rollback backup container for slug {slug}"))
                else:
                    stale_containers.append((c, f"Stale rollback backup container for active slug {slug}"))
                continue

            # Check if container name matches any known service slug
            # This is tricky because custom names could be anything.
            # We'll rely on the labels or specific patterns above for safety.

        report_lines = []
        report_lines.append("# Runtime Cleanup Report\n")

        if not stale_containers:
            self.stdout.write("No stale containers found using legacy patterns.")
            report_lines.append("No stale containers found using legacy patterns.")
        else:
            self.stdout.write(f"Found {len(stale_containers)} stale containers:")
            for c, reason in stale_containers:
                line = f"- {c.name} ({c.id[:10]}): {reason}"
                self.stdout.write(line)
                report_lines.append(line)

                if apply:
                    self.stdout.write(f"  Removing {c.name}...")
                    try:
                        c.stop(timeout=10)
                        c.remove(force=True)
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"  Failed to remove {c.name}: {e}"))

        with open("/tmp/runtime-cleanup-report.md", "w") as f:
            f.write("\n".join(report_lines))
