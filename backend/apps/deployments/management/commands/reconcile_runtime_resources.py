import logging

import docker
from django.core.management.base import BaseCommand

from apps.deployments.models_addons import Addon
from apps.deployments.models_core import Service
from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Reconcile Docker runtime state with Django DB state to find and clean orphaned resources."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only report orphans, do not delete them.',
        )
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually delete orphaned containers and volumes.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        apply = options['apply']

        if not dry_run and not apply:
            self.stdout.write(self.style.ERROR("Must specify either --dry-run or --apply"))
            return

        self.stdout.write("Starting runtime resource reconciliation...")
        try:
            client = docker.from_env()
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to connect to Docker: {e}"))
            return

        active_service_ids = {str(sid) for sid in Service.objects.exclude(status='DELETED').values_list('id', flat=True)}
        active_addon_ids = {str(aid) for aid in Addon.objects.exclude(status='DELETED').values_list('id', flat=True)}

        orchestrator = DeletionOrchestrator()

        all_containers = client.containers.list(all=True)
        orphans = []

        # 1. Detect orphaned containers
        for c in all_containers:
            labels = c.labels
            if labels.get('smsly.managed') != 'true' and labels.get('managed_by') != 'smsly-hosting':
                continue

            service_id = labels.get('smsly.service_id')
            addon_id = labels.get('smsly.addon_id')

            is_orphan = False
            reason = ""

            if service_id and service_id not in active_service_ids:
                is_orphan = True
                reason = f"Service {service_id} deleted in DB"
            elif addon_id and addon_id not in active_addon_ids:
                is_orphan = True
                reason = f"Addon {addon_id} deleted in DB"

            if not service_id and not addon_id:
                continue

            if is_orphan:
                orphans.append((c, reason))

        if not orphans:
            self.stdout.write(self.style.SUCCESS("No orphaned containers found."))
        else:
            self.stdout.write(self.style.WARNING(f"Found {len(orphans)} orphaned containers:"))
            for c, reason in orphans:
                self.stdout.write(f"  - {c.name} ({c.id[:10]}): {reason}")

                if apply:
                    self.stdout.write(f"    Removing {c.name}...")
                    orchestrator._safe_remove_container(c)

        self.stdout.write(self.style.SUCCESS("Reconciliation complete."))
