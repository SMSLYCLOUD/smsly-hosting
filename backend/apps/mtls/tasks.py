"""
mTLS Celery tasks
=================
Background tasks for mTLS management: auto-injection, SVID rotation tracking.
"""

import logging
import time

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK, RETRY_DELAY_SECONDS

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=2,
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    name="apps.mtls.tasks.inject_mtls_task",
)
def inject_mtls_task(self, service_id: str):
    """
    Hot-swap running containers to inject SPIRE mTLS mounts.

    This is triggered automatically when mTLS is enabled on a running service.
    Commits the container, creates a new one with SPIRE volumes/env vars,
    and swaps traffic with minimal downtime (~2-5s).
    """
    from apps.deployments.models import Service
    from apps.mtls.models import MtlsConfig
    from apps.deployments.services.mtls_integration import (
        get_mtls_labels,
        get_mtls_env_vars,
        get_mtls_docker_run_volumes,
    )

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        logger.error("Service %s not found, skipping mTLS injection", service_id)
        return

    try:
        config = service.mtls_config
        if not config.enabled:
            logger.info("mTLS disabled for %s, skipping injection", service.name)
            return
    except MtlsConfig.DoesNotExist:
        logger.error("No mTLS config for %s, skipping injection", service.name)
        return

    try:
        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()

        # Find containers for this service
        containers = client.containers.list(
            filters={"label": "managed_by=smsly-hosting"},
        )

        service_containers = [
            c for c in containers
            if (c.labels or {}).get("smsly.blue_green.canonical_name") == service.name
        ]

        if not service_containers:
            logger.info("No running containers for %s, nothing to inject", service.name)
            return

        for container in service_containers:
            _swap_container_with_mtls(client, container, service)

        logger.info("mTLS injection completed for %s (%d containers)",
                     service.name, len(service_containers))

    except Exception as exc:
        logger.error("mTLS injection failed for %s: %s", service.name, exc)
        raise self.retry(exc=exc, countdown=RETRY_DELAY_SECONDS)


def _swap_container_with_mtls(client, old_container, service):
    """Commit old container, create new one with mTLS mounts, swap."""
    from apps.deployments.services.mtls_integration import (
        get_mtls_labels,
        get_mtls_env_vars,
        get_mtls_docker_run_volumes,
    )

    name = old_container.name
    new_name = f"{name}-mtls-{int(time.time())}"

    logger.info("Hot-swapping container %s for mTLS injection", name)

    # Step 1: Commit
    repo = f"mtls-swap/{name}"
    tag = f"pre-mtls-{int(time.time())}"
    old_container.commit(repository=repo, tag=tag)
    logger.debug("Committed %s to %s:%s", name, repo, tag)

    # Step 2: Stop old container
    old_container.stop(timeout=10)

    # Step 3: Create new container with mTLS mounts
    mtls_labels = get_mtls_labels(service)
    mtls_env = get_mtls_env_vars(service)
    mtls_volumes = get_mtls_docker_run_volumes(service)

    # Merge labels
    new_labels = dict(old_container.labels)
    new_labels.update(mtls_labels)

    # Merge environment
    new_env = {}
    for env_str in old_container.attrs.get("Config", {}).get("Env") or []:
        if "=" in env_str:
            k, v = env_str.split("=", 1)
            new_env[k] = v
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

    # Get network config
    network_config = old_container.attrs.get("NetworkSettings", {}).get("Networks") or {}
    primary_network = None
    for net_name in network_config:
        if net_name != "bridge":
            primary_network = net_name
            break

    try:
        new_container = client.containers.run(
            image=f"{repo}:{tag}",
            name=new_name,
            detach=True,
            restart_policy=old_container.attrs.get("HostConfig", {}).get(
                "RestartPolicy", {"Name": "unless-stopped"}
            ),
            network=primary_network or "bridge",
            labels=new_labels,
            environment=new_env,
            volumes=new_volumes,
            security_opt=old_container.attrs.get("HostConfig", {}).get("SecurityOpt", [
                "no-new-privileges:true", "apparmor:docker-default",
            ]),
            cap_drop=old_container.attrs.get("HostConfig", {}).get("CapDrop", ["ALL"]),
            cap_add=old_container.attrs.get("HostConfig", {}).get("CapAdd", [
                "NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID",
            ]),
            mem_limit=old_container.attrs.get("HostConfig", {}).get("Memory"),
            nano_cpus=old_container.attrs.get("HostConfig", {}).get("NanoCpus"),
            pids_limit=old_container.attrs.get("HostConfig", {}).get("PidsLimit"),
            runtime=old_container.attrs.get("HostConfig", {}).get("Runtime"),
        )
    except Exception as exc:
        # Rollback: restart old container
        logger.error("Failed to create new container %s: %s, rolling back", new_name, exc)
        old_container.start()
        raise

    # Step 4: Remove old container
    try:
        old_container.remove(force=True)
    except Exception as exc:
        logger.warning("Could not remove old container %s: %s", name, exc)

    # Cleanup old image
    try:
        client.images.remove(f"{repo}:{tag}", force=True)
    except Exception:
        pass

    logger.info("Swapped %s -> %s with SPIRE mTLS mounts", name, new_name)
