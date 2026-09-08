"""
mTLS Celery tasks
=================
Background tasks for mTLS management: auto-injection, SVID rotation tracking.
"""

import logging
import time
import datetime

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK, RETRY_DELAY_STANDARD

logger = logging.getLogger(__name__)


def _parse_sidecar_identity(certs_data):
    """Extract the served identity SVID (URI + expiry) from Envoy /certs.

    Returns (spiffe_uri, expiry_utc) or (None, None) when no service
    identity certificate is present.
    """
    certificates = (certs_data or {}).get("certificates") or []
    best = None
    for group in certificates:
        if not isinstance(group, dict):
            continue
        for cert in group.get("cert_chain") or []:
            if not isinstance(cert, dict):
                continue
            uris = [
                (alt or {}).get("uri", "")
                for alt in (cert.get("subject_alt_names") or [])
                if isinstance(alt, dict)
            ]
            identity_uris = [u for u in uris if "/service/" in u]
            if not identity_uris:
                continue
            try:
                expiry = datetime.datetime.strptime(
                    cert.get("expiration_time") or "", "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=datetime.timezone.utc)
            except (ValueError, TypeError):
                continue
            if best is None or expiry < best[1]:
                best = (identity_uris[0], expiry)
    return best if best else (None, None)


def _read_sidecar_certs(client, sidecar_name):
    """Fetch and parse Envoy admin /certs from a sidecar container."""
    import json

    try:
        container = client.containers.get(sidecar_name)
    except Exception:
        return None
    try:
        if getattr(container, "status", "") != "running":
            container.reload()
            if getattr(container, "status", "") != "running":
                return None
        result = container.exec_run(
            ["sh", "-c", "curl -fsS --max-time 8 http://127.0.0.1:9901/certs"],
            demux=False,
        )
        if result.exit_code != 0:
            return None
        output = result.output
        if isinstance(output, (bytes, bytearray)):
            output = output.decode(errors="replace")
        return json.loads(output)
    except Exception as exc:
        logger.debug("Sidecar /certs unavailable for %s: %s", sidecar_name, exc)
        return None
@shared_task(
    name="apps.mtls.tasks.sync_svid_metadata_task",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def sync_svid_metadata_task():
    """Sync SVID expiry metadata from Envoy sidecar admin APIs.

    The primary source is each sidecar's ``GET /certs`` (SDS-issued
    identity chain with expiry). The legacy workload
    ``/opt/spire/svids/cert.pem`` openssl probe is kept as a fallback
    for setups without a sidecar — but most app images mount no SVID
    files at all, which is why the page showed "missing" forever.
    """
    from apps.cloud.docker_client import get_docker_client
    from apps.mtls.models import MtlsConfig
    from apps.mtls.services.envoy_sidecar import EnvoySidecar

    client = get_docker_client()
    synced = 0
    checked = 0
    for config in MtlsConfig.objects.filter(enabled=True).select_related("service"):
        service = config.service
        checked += 1
        uri, expiry = None, None
        try:
            certs = _read_sidecar_certs(
                client, EnvoySidecar.get_sidecar_name(service)
            )
            if certs:
                uri, expiry = _parse_sidecar_identity(certs)
        except Exception as exc:
            logger.debug("Sidecar SVID read failed for %s: %s", service.name, exc)
        if expiry is None:
            uri, expiry = _read_workload_cert_expiry(client, service)
        if expiry is None:
            continue
        config.svid_expiry = expiry
        config.last_rotation = timezone.now()
        config.save(update_fields=["svid_expiry", "last_rotation", "updated_at"])
        synced += 1
        logger.info(
            "SVID metadata synced for %s (uri=%s expiry=%s)",
            service.name, uri or "n/a", expiry.isoformat(),
        )
    return {"synced": synced, "checked": checked}


def _read_workload_cert_expiry(client, service):
    """Legacy fallback: openssl probe of cert.pem inside the workload."""
    try:
        container = next(iter(client.containers.list(
            filters={"label": f"smsly.blue_green.canonical_name={service.name}"}
        )), None)
        if not container:
            return None, None
        result = container.exec_run([
            "sh", "-c",
            "openssl x509 -in /opt/spire/svids/cert.pem -noout -enddate -startdate",
        ])
        if result.exit_code != 0:
            return None, None
        values = {}
        for line in result.output.decode(errors="replace").splitlines():
            key, _, value = line.partition("=")
            values[key.lower()] = value.strip()
        expiry = datetime.datetime.strptime(
            values["notafter"], "%b %d %H:%M:%S %Y %Z"
        ).replace(tzinfo=datetime.timezone.utc)
        return None, expiry
    except Exception as exc:
        logger.debug("SVID metadata unavailable for %s: %s", service.name, exc)
        return None, None


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
        merge_docker_volumes,
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
        raise self.retry(exc=exc, countdown=RETRY_DELAY_STANDARD)


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
    new_volumes = merge_docker_volumes(new_volumes, mtls_volumes)

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
