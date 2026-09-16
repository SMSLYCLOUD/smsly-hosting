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

from apps.deployments.constants import (
    TASK_TIME_LIMIT_QUICK,
    TASK_TIME_LIMIT_STANDARD,
    RETRY_DELAY_STANDARD,
)

logger = logging.getLogger(__name__)


def _parse_expiry(raw):
    """Parse an Envoy /certs expiration_time into an aware UTC datetime.

    Envoy emits ``%Y-%m-%dT%H:%M:%SZ`` but fractional seconds
    (``...T15:26:43.123Z`` / nanos) have been observed from SDS dumps.
    Returns None when unparseable — callers treat that as "no SVID yet"
    rather than crashing.
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.datetime.strptime(text, fmt).replace(
                tzinfo=datetime.timezone.utc
            )
        except (ValueError, TypeError):
            continue
    try:
        # Handles offsets: "...+00:00" and "Z" with fractions.
        iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
        parsed = datetime.datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed
    except (ValueError, TypeError):
        return None


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
            expiry = _parse_expiry(cert.get("expiration_time"))
            if expiry is None:
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
def sync_svid_for_service(service, client=None):
    """Best-effort immediate SVID metadata sync for one service.

    Called right after a sidecar becomes ready so the UI stops showing
    "Missing" without waiting for the hourly beat. Never raises —
    returns True when svid_expiry was persisted.
    """
    try:
        from apps.cloud.docker_client import get_docker_client
        from apps.mtls.models import MtlsConfig
        from apps.mtls.services.envoy_sidecar import EnvoySidecar

        client = client or get_docker_client()
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
            return False
        MtlsConfig.objects.filter(service=service).update(
            svid_expiry=expiry, last_rotation=timezone.now()
        )
        logger.info(
            "SVID metadata synced for %s (uri=%s expiry=%s)",
            service.name, uri or "n/a", expiry.isoformat(),
        )
        return True
    except Exception as exc:
        logger.debug("SVID sync skipped for %s: %s", getattr(service, "name", "?"), exc)
        return False


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

    client = get_docker_client()
    synced = 0
    checked = 0
    for config in MtlsConfig.objects.filter(enabled=True).select_related("service"):
        checked += 1
        if sync_svid_for_service(config.service, client=client):
            synced += 1
    return {"synced": synced, "checked": checked}


def _ensure_spire_entry_best_effort(service, mtls_config=None) -> None:
    """Ensure the SPIRE registration entry exists (never raises).

    The deploy path creates the entry BEFORE the sidecar requests an
    SVID; without it the agent denies the workload and a remounted
    sidecar stays SVID-less forever while the beat reports success.
    Same trust-domain branching as the deploy pipeline (platform.local
    -> platform server, else ecosystem server). Failures only log —
    the remount + sync below still report clearly.
    """
    try:
        from apps.deployments.tasks_spiffe import (
            _create_spire_entry,
            _entry_path,
            _list_spire_entries,
            _live_ecosystem_agent_id,
            _live_platform_agent_id,
            PLATFORM_SPIFFE_TRUST_DOMAIN,
        )
        trust_domain = ""
        try:
            trust_domain = str(
                getattr(mtls_config, "trust_domain", "")
                or getattr(getattr(service, "mtls_config", None), "trust_domain", "")
                or ""
            ).strip()
        except Exception:
            pass
        if trust_domain == PLATFORM_SPIFFE_TRUST_DOMAIN:
            from apps.deployments.tasks_spiffe import (
                _list_platform_spire_entries,
            )
            listed = _list_platform_spire_entries()
        else:
            listed = _list_spire_entries()
        if listed is None:
            # Server unreachable — abort, don't blind-create. The next
            # 15m tick retries; a blind create here only adds log noise
            # and duplicate attempts while the daemon is down.
            logger.debug("SPIRE entry list unavailable for %s — skipping ensure", service.name)
            return
        want_path = f"/service/{service.name}"
        if any(_entry_path(e) == want_path for e in listed):
            return
        created = False
        if trust_domain == PLATFORM_SPIFFE_TRUST_DOMAIN:
            created = bool(_create_spire_entry(
                service.name,
                parent_id=_live_platform_agent_id(),
                trust_domain=PLATFORM_SPIFFE_TRUST_DOMAIN,
            ))
        else:
            created = bool(_create_spire_entry(
                service.name,
                parent_id=_live_ecosystem_agent_id(),
            ))
        if created:
            logger.info("Created missing SPIRE entry for %s (repair beat)", service.name)
    except Exception as exc:
        logger.debug("SPIRE entry ensure skipped for %s: %s", getattr(service, "name", "?"), exc)


@shared_task(
    name="apps.mtls.tasks.repair_stale_sidecars_task",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def repair_stale_sidecars_task():
    """Remount SVID-less sidecars and remove orphans (every 15m).

    Closes the loop the manual repair endpoint left open: sidecars
    injected before the 2026-09-15 resolver fix mount the empty decoy
    volume, and the deploy path used to keep them (``already_running``)
    — so without this beat nothing ever healed them and the Services
    page showed "SVID Missing" forever.

    Per enabled-sidecar service (never raises — best-effort per
    service, errors are collected into the result):
    * app running + sidecar stale/missing -> remount (or fresh inject)
    * app gone + sidecar present (any state) -> remove the orphan so it
      stops pinning decoy volumes (redeploy re-injects on return)
    * deploy in flight (non-terminal Deployment touched in the last
      30m) -> skip, the pipeline owns the sidecar right now

    Phase 2 sweeps sidecar containers whose Service row is gone
    entirely (the repair endpoint only iterates live rows).
    """
    import datetime

    from apps.cloud.docker_client import get_docker_client
    from apps.deployments.models import Deployment, Service
    from apps.mtls.models import MtlsConfig
    from apps.mtls.services.envoy_sidecar import EnvoySidecar

    result = {
        "remounted": [], "injected": [], "orphans_removed": [],
        "skipped_in_flight": [], "errors": [],
    }
    try:
        client = get_docker_client()
    except Exception as exc:
        logger.warning("Sidecar repair skipped (docker unavailable): %s", exc)
        result["errors"].append(f"docker unavailable: {exc}")
        return result

    try:
        cutoff = timezone.now() - datetime.timedelta(minutes=30)
        # In-flight = every non-terminal status (a recent one means the
        # pipeline owns the sidecar right now). Terminal: *_FAILED,
        # ACTIVE, CANCELLED, INACTIVE, ROLLED_BACK.
        in_flight = set(
            Deployment.objects.filter(updated_at__gt=cutoff)
            .filter(status__in=[
                Deployment.Status.QUEUED, Deployment.Status.REVIEW,
                Deployment.Status.BUILDING,
                Deployment.Status.AWAITING_APPROVAL,
                Deployment.Status.BACKUP_RUNNING,
                Deployment.Status.MIGRATION_PLANNING,
                Deployment.Status.MIGRATION_RUNNING,
                Deployment.Status.DEPLOYING,
                Deployment.Status.HEALTH_CHECK, Deployment.Status.STAGED,
                Deployment.Status.ROLLING_BACK,
            ])
            .values_list("service_id", flat=True)
        )
    except Exception as exc:
        logger.debug("In-flight guard unavailable, proceeding: %s", exc)
        in_flight = set()

    for config in MtlsConfig.objects.filter(
        enabled=True, sidecar_enabled=True
    ).select_related("service"):
        svc = config.service
        try:
            if svc.id in in_flight:
                result["skipped_in_flight"].append(svc.name)
                continue
            app = EnvoySidecar._find_main_container(client, svc)
            sidecar_state = EnvoySidecar.get_sidecar_status(svc).get("status")
            if app is None:
                if sidecar_state not in (None, "not_found"):
                    removed = EnvoySidecar.remove_sidecar(svc)
                    if removed.get("status") == "removed":
                        result["orphans_removed"].append(svc.name)
                        logger.info(
                            "Removed orphan sidecar for %s (no app container)",
                            svc.name,
                        )
                continue
            out = None
            try:
                # Entry BEFORE remount: with no matching registration the
                # agent denies the workload and the fresh sidecar stays
                # SVID-less while we report success.
                _ensure_spire_entry_best_effort(svc, getattr(svc, "mtls_config", config))
                out = EnvoySidecar.remount_if_stale(svc)
            except Exception as exc:
                logger.warning("Sidecar repair failed for %s: %s", svc.name, exc)
                result["errors"].append(f"{svc.name}: {exc}")
                continue
            if out.get("remounted"):
                result["remounted"].append(svc.name)
            elif out.get("status") == "injected":
                result["injected"].append(svc.name)
            else:
                continue
            # Verify the healed sidecar actually serves an SVID (fast
            # /certs read, not a full 120s wait — the beat must not
            # serialize behind slow SDS under host pressure). A missing
            # SVID lands in errors so the dashboard signal stays honest
            # instead of reporting a successful heal with no identity.
            try:
                if not sync_svid_for_service(svc, client=client):
                    result["errors"].append(
                        f"{svc.name}: sidecar healed but no SVID issued yet "
                        "(entry/agent may still be converging)"
                    )
            except Exception as exc:
                logger.debug("SVID verify skipped for %s: %s", svc.name, exc)
        except Exception as exc:
            logger.warning("Sidecar repair failed for %s: %s", svc.name, exc)
            result["errors"].append(f"{svc.name}: {exc}")

    # Phase 2: sidecars whose Service row no longer exists.
    try:
        for container in (
            client.containers.list(all=True, filters={"label": "envoy_sidecar=true"}) or []
        ):
            try:
                labels = getattr(container, "labels", None) or {}
                cname = str(
                    labels.get("smsly.blue_green.canonical_name")
                    or labels.get("com.paas.service") or ""
                ).strip()
                if not cname or Service.objects.filter(name=cname).exists():
                    continue
                try:
                    container.stop(timeout=5)
                except Exception:
                    pass
                try:
                    container.remove(force=True)
                except Exception:
                    continue
                try:
                    EnvoySidecar._remove_config_file(
                        type("Svc", (), {"name": cname})()
                    )
                except Exception:
                    pass
                result["orphans_removed"].append(getattr(container, "name", cname))
                logger.info("Removed row-less orphan sidecar %s", cname)
            except Exception as exc:
                logger.debug("Row-less orphan sweep skipped a container: %s", exc)
    except Exception as exc:
        logger.debug("Row-less orphan sweep unavailable: %s", exc)
    return result


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
