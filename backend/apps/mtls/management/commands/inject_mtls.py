"""
inject_mtls management command
==============================
Hot-swap a running container to inject SPIRE volumes, labels, and env vars.

For containers that are already running when mTLS is enabled, this command
commits the container, creates a new one with mTLS mounts, and swaps traffic.

Usage:
    python manage.py inject_mtls <service_name> [--dry-run]
    python manage.py inject_mtls --all [--dry-run]
"""

import logging
import shlex
import sys
import time

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Hot-swap running containers to inject SPIRE mTLS mounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "service_name",
            nargs="?",
            help="Name of the service to inject mTLS into.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="inject_all",
            help="Inject mTLS into all running mTLS-enabled services.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        inject_all = options["inject_all"]
        service_name = options.get("service_name")

        if not inject_all and not service_name:
            raise CommandError("Provide a service name or use --all.")

        from apps.deployments.models import Service
        from apps.mtls.models import MtlsConfig

        if inject_all:
            services = Service.objects.filter(
                mtls_config__enabled=True,
                status="RUNNING",
            ).select_related("mtls_config")
        else:
            try:
                service = Service.objects.get(name=service_name)
            except Service.DoesNotExist:
                raise CommandError(f"Service '{service_name}' not found.")
            try:
                config = service.mtls_config
                if not config.enabled:
                    raise CommandError(f"mTLS is not enabled for '{service_name}'.")
            except MtlsConfig.DoesNotExist:
                raise CommandError(f"No mTLS config for '{service_name}'. Enable mTLS first.")
            services = [service]

        if not services:
            self.stdout.write("No running mTLS-enabled services found.")
            return

        for service in services:
            self._inject_service(service, dry_run)

    def _inject_service(self, service, dry_run=False):
        """Hot-swap a single service's container with mTLS mounts."""
        from apps.deployments.services.mtls_integration import (
            get_mtls_labels,
            get_mtls_env_vars,
            get_mtls_docker_run_args,
        )
        from apps.cloud.docker_client import get_docker_client

        self.stdout.write(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {service.name}")

        docker_client = get_docker_client()

        # Find running container(s) for this service
        containers = docker_client.containers.list(
            filters={"label": f"managed_by=smsly-hosting"},
        )

        service_containers = []
        for ctr in containers:
            labels = ctr.labels or {}
            canonical = labels.get("smsly.blue_green.canonical_name", "")
            if canonical == service.name:
                service_containers.append(ctr)

        if not service_containers:
            self.stdout.write(self.style.WARNING(f"  No running containers found for {service.name}"))
            return

        for container in service_containers:
            self._swap_container(container, service, dry_run)

    def _swap_container(self, old_container, service, dry_run=False):
        """Commit old container, create new one with mTLS mounts, swap."""
        from apps.deployments.services.mtls_integration import (
            get_mtls_labels,
            get_mtls_env_vars,
            get_mtls_docker_run_volumes,
        )
        from apps.cloud.docker_client import get_docker_client
        import docker as docker_lib

        client = get_docker_client()
        name = old_container.name
        new_name = f"{name}-mtls-{int(time.time())}"

        self.stdout.write(f"  Container: {name} (status: {old_container.status})")

        if dry_run:
            self.stdout.write(f"  Would commit, create {new_name} with SPIRE mounts, and swap.")
            return

        # Step 1: Commit the running container to preserve its filesystem
        self.stdout.write(f"  Step 1/4: Committing container...")
        repo = f"mtls-swap/{name}"
        tag = f"pre-mtls-{int(time.time())}"
        old_container.commit(repository=repo, tag=tag)
        self.stdout.write(f"  Committed to {repo}:{tag}")

        # Step 2: Stop old container briefly
        self.stdout.write(f"  Step 2/4: Stopping old container...")
        old_container.stop(timeout=10)

        # Step 3: Create new container with mTLS mounts
        self.stdout.write(f"  Step 3/4: Creating new container with SPIRE mounts...")

        # Get mTLS configuration
        mtls_labels = get_mtls_labels(service)
        mtls_env = get_mtls_env_vars(service)
        mtls_volumes = get_mtls_docker_run_volumes(service)

        # Merge labels
        new_labels = dict(old_container.labels)
        new_labels.update(mtls_labels)

        # Merge environment
        new_env = dict(old_container.attrs.get("Config", {}).get("Env") or [])
        # Remove old env vars that we're replacing
        for key in mtls_env:
            new_env.pop(key, None)
        new_env.update(mtls_env)

        # Build volume mounts
        new_volumes = {}
        for vol in old_container.attrs.get("Mounts") or []:
            src = vol.get("Source", "")
            dst = vol.get("Destination", "")
            mode = vol.get("Mode", "rw")
            if src and dst:
                new_volumes[src] = {"bind": dst, "mode": mode}
        new_volumes.update(mtls_volumes)

        # Get network config from old container
        network_config = old_container.attrs.get("NetworkSettings", {}).get("Networks") or {}
        primary_network = None
        for net_name, net_config in network_config.items():
            if net_name != "bridge":
                primary_network = net_name
                break

        # Create new container
        try:
            new_container = client.containers.run(
                image=f"{repo}:{tag}",
                name=new_name,
                detach=True,
                restart_policy=old_container.attrs.get("HostConfig", {}).get("RestartPolicy", {"Name": "unless-stopped"}),
                network=primary_network or "bridge",
                labels=new_labels,
                environment=new_env,
                volumes=new_volumes,
                security_opt=old_container.attrs.get("HostConfig", {}).get("SecurityOpt", [
                    "no-new-privileges:true", "apparmor:docker-default"
                ]),
                cap_drop=old_container.attrs.get("HostConfig", {}).get("CapDrop", ["ALL"]),
                cap_add=old_container.attrs.get("HostConfig", {}).get("CapAdd", [
                    "NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"
                ]),
                mem_limit=old_container.attrs.get("HostConfig", {}).get("Memory"),
                nano_cpus=old_container.attrs.get("HostConfig", {}).get("NanoCpus"),
                pids_limit=old_container.attrs.get("HostConfig", {}).get("PidsLimit"),
                runtime=old_container.attrs.get("HostConfig", {}).get("Runtime"),
            )
        except Exception as exc:
            # Rollback: restart old container
            self.stdout.write(self.style.ERROR(f"  Failed to create new container: {exc}"))
            self.stdout.write(f"  Rolling back: restarting old container...")
            old_container.start()
            return

        # Step 4: Remove old container
        self.stdout.write(f"  Step 4/4: Removing old container...")
        try:
            old_container.remove(force=True)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"  Warning: could not remove old container: {exc}"))

        self.stdout.write(self.style.SUCCESS(
            f"  Swapped {name} -> {new_name} with SPIRE mTLS mounts"
        ))

        # Cleanup old image
        try:
            client.images.remove(f"{repo}:{tag}", force=True)
        except Exception:
            pass
