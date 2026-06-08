"""
Management command: deploy docker-labels exporters to all remote nodes
and update Prometheus target files.
"""
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Deploy docker-labels exporter on all remote nodes and update Prometheus targets"

    def add_arguments(self, parser):
        parser.add_argument(
            "--node",
            type=str,
            default="",
            help="Only deploy to a specific node (by name or ID)",
        )
        parser.add_argument(
            "--targets-only",
            action="store_true",
            help="Only write Prometheus target files, don't deploy containers",
        )

    def handle(self, *args, **options):
        from apps.deployments.models_core import ManagedServer
        from apps.deployments.services.prometheus_targets import (
            deploy_docker_labels_exporter_on_node,
            write_docker_labels_targets,
        )

        node_filter = options.get("node", "")

        if node_filter:
            servers = ManagedServer.objects.filter(
                is_primary=False,
                status=ManagedServer.Status.ONLINE,
            ).filter(
                name__icontains=node_filter,
            )
        else:
            servers = ManagedServer.objects.filter(
                is_primary=False,
                status=ManagedServer.Status.ONLINE,
            )

        if options.get("targets_only"):
            write_docker_labels_targets()
            self.stdout.write(self.style.SUCCESS("Prometheus target files updated"))
            return

        deployed = 0
        skipped = 0
        failed = 0

        for server in servers:
            if not server.ssh_key and not server.ssh_password:
                self.stdout.write(
                    self.style.WARNING(f"Skipping {server.name} — no SSH credentials")
                )
                skipped += 1
                continue

            self.stdout.write(f"Deploying to {server.name} ({server.host})...")
            success = deploy_docker_labels_exporter_on_node(server)
            if success:
                deployed += 1
                self.stdout.write(self.style.SUCCESS(f"  ✓ {server.name}"))
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  ✗ {server.name}"))

        # Update target files after deployment
        write_docker_labels_targets()

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {deployed} deployed, {skipped} skipped, {failed} failed"
            )
        )
