"""Live application of Service CPU/RAM limits to running containers.

Docker applies ``--cpus``/``--memory`` at create time, so changing
``Service.cpu_cores``/``memory_mb`` normally waits for the next redeploy.
These helpers close that gap with ``docker update`` (CPU and memory limits
are live-updatable; only a memory *decrease* below current usage fails,
in which case the values still apply on the next deploy).

Remote-node services are reported as skipped — their limits apply on the
next deploy (the VPA beat covers remote nodes on its own cadence).
"""
import logging

logger = logging.getLogger(__name__)


def _running_service_containers(service) -> list:
    """Return running local containers belonging to ``service``."""
    try:
        import docker
        client = docker.from_env()
    except Exception as exc:
        logger.warning("Docker unavailable for live limit apply: %s", exc)
        return []
    found = []
    try:
        found = client.containers.list(
            filters={"label": f"smsly.service_id={service.id}"},
        )
    except Exception as exc:
        logger.debug("Label lookup failed for service %s: %s", service.id, exc)
    if not found:
        # Fallback for containers created before the label existed.
        try:
            candidate = client.containers.get(service.name)
            if getattr(candidate, "status", "") == "running":
                found = [candidate]
        except Exception:
            pass
    return [c for c in found if getattr(c, "status", "") == "running"]


def apply_service_resource_limits(service) -> dict:
    """Apply the service's stored CPU/RAM limits to its running containers.

    Returns {"updated": [...names], "skipped_containers": [{name, reason}],
    "skipped": reason|None, "errors": [...]}; never raises — callers
    (signals, tasks, one-off passes) must not fail the operation that
    triggered them. gVisor runsc containers are skipped: runsc does not
    support live updates (exit 128) — they pick up limits on next deploy.
    """
    try:
        cpus = float(getattr(service, "cpu_cores", 0) or 0)
    except (TypeError, ValueError):
        cpus = 0.0
    try:
        mem_mb = int(getattr(service, "memory_mb", 0) or 0)
    except (TypeError, ValueError):
        mem_mb = 0
    if cpus <= 0 and mem_mb <= 0:
        return {"updated": [], "skipped_containers": [], "skipped": "no limits set", "errors": []}
    if getattr(service, "server_id", None):
        return {"updated": [], "skipped_containers": [], "skipped": "remote node — applies on next deploy", "errors": []}

    update_kwargs = {}
    if cpus > 0:
        # NOTE: the update API takes quota/period (like the VPA task) —
        # nano_cpus is create-only and raises TypeError here.
        update_kwargs["cpu_period"] = 100000
        update_kwargs["cpu_quota"] = int(cpus * 100000)
        update_kwargs["cpu_shares"] = max(2, int(cpus * 1024))
    if mem_mb > 0:
        # memoryswap must move together with memory (Docker keeps the
        # default 2x ratio); otherwise raising memory fails with 409.
        update_kwargs["mem_limit"] = f"{mem_mb}m"
        update_kwargs["memswap_limit"] = f"{mem_mb * 2}m"

    updated, skipped_containers, errors = [], [], []
    for container in _running_service_containers(service):
        try:
            runtime = str(((getattr(container, "attrs", None) or {}).get("HostConfig", {}) or {}).get("Runtime", "") or "")
        except Exception:
            runtime = ""
        if runtime.startswith("runsc"):
            skipped_containers.append({"name": container.name, "reason": "gVisor runsc — needs redeploy"})
            continue
        try:
            container.update(**update_kwargs)
            updated.append(container.name)
        except Exception as exc:
            errors.append(f"{container.name}: {exc}")
            logger.warning("Live limit apply failed for %s: %s", container.name, exc)
    return {"updated": updated, "skipped_containers": skipped_containers, "skipped": None, "errors": errors}
