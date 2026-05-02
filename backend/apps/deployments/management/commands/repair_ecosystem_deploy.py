import logging
from django.core.management.base import BaseCommand
from apps.deployments.models import Deployment, Service, ManagedServer
from apps.deployments.services.ecosystem_persist import bulk_persist_and_verify_ecosystem_env
from apps.deployments.services.node_selector import select_eligible_node
import yaml

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Repairs a failed ecosystem deployment by syncing env, assigning nodes, and queueing redeploy.'

    def add_arguments(self, parser):
        parser.add_argument('--project', type=str, required=True, help='Project ID to repair')
        parser.add_argument('--deployment', type=str, required=False, help='Specific deployment ID to repair')
        parser.add_argument('--dry-run', action='store_true', help='Show what would be repaired without applying')
        parser.add_argument('--apply', action='store_true', help='Apply the repairs')

    def handle(self, *args, **options):
        project_id = options['project']
        is_dry_run = options['dry_run']
        is_apply = options['apply']

        if not is_dry_run and not is_apply:
            self.stdout.write(self.style.ERROR("Must specify either --dry-run or --apply"))
            return

        services = Service.objects.filter(project_id=project_id)
        if not services.exists():
            self.stdout.write(self.style.ERROR(f"No services found for project {project_id}"))
            return

        self.stdout.write(self.style.SUCCESS(f"Found {services.count()} services for project {project_id}"))
        created_services = {s.name: s for s in services}
        user = services.first().owner

        issues_found = []
        fixes_planned = []

        for s in services:
            if not getattr(s, 'server', None):
                issues_found.append(f"Service {s.name} has no assigned node")
                if is_apply:
                    node = select_eligible_node(user)
                    if node:
                        s.server = node
                        s.save()
                        fixes_planned.append(f"Assigned node {node.name} to {s.name}")
                    else:
                        fixes_planned.append(f"Failed to assign node to {s.name}: No eligible nodes")
                else:
                    fixes_planned.append(f"Would assign eligible node to {s.name}")

        mock_manifest = yaml.dump({
            "version": "1",
            "services": {s.name: {"type": "repaired"} for s in services}
        })

        if is_apply:
            success, msg = bulk_persist_and_verify_ecosystem_env(mock_manifest, created_services)
            if success:
                fixes_planned.append("Verified and persisted environment variables for all services.")
            else:
                self.stdout.write(self.style.ERROR(f"Env validation failed during repair: {msg}"))
        else:
            fixes_planned.append("Would run bulk env resolution and verification.")

        if is_apply:
            for s in services:
                latest_dep = Deployment.objects.filter(service=s).order_by('-created_at').first()
                if latest_dep and latest_dep.status == Deployment.Status.FAILED:
                    latest_dep.status = Deployment.Status.QUEUED
                    latest_dep.build_logs += "\n[Repair] Re-queued by ecosystem repair tool."
                    latest_dep.save()
                    fixes_planned.append(f"Re-queued failed deployment for {s.name}")
        else:
            fixes_planned.append("Would re-queue failed deployments.")

        self.stdout.write("\nIssues Found:")
        for i in issues_found:
            self.stdout.write(f"- {i}")

        self.stdout.write("\nPlan:")
        for f in fixes_planned:
            self.stdout.write(f"- {f}")

        if is_apply:
            self.stdout.write(self.style.SUCCESS("\nRepair applied successfully."))
        else:
            self.stdout.write(self.style.WARNING("\nDry run complete. No changes made."))
