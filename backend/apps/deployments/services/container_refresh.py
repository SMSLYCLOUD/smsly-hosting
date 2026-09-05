"""Recreate a running service container with fresh config, without rebuilding.

Linux processes snapshot their environment at exec: editing env vars in
the DB never reaches an already-running container, and ``docker restart``
keeps the OLD environment (restart != recreate). The only instant path
is stop + recreate from the SAME image with fresh env — seconds of
downtime for a process boot, no build, no registry pull.

Safety:
  * the old container is renamed (not removed) until the replacement
    reports running; any failure rolls back (rename back + start).
  * networks/aliases, labels, mounts, restart policy, runtime, and
    resource limits are all cloned from the live container, so routing
    (Traefik labels, DNS aliases) and isolation survive intact.
  * resource limits come from the Service row (converging drift), env
    from EnvironmentVariable rows + mTLS injection (mirroring spawn).
  * remote-node services are refused — run where the code is current.
"""
import logging
import time

logger = logging.getLogger(__name__)


class ContainerRefreshError(RuntimeError):
    """Fatal, user-facing recreation failure (rollback already attempted)."""


def _docker_client():
    import docker
    return docker.from_env()


def _resolve_target_container(service, client, container_id=None):
    """Return the running container to refresh, or None."""
    candidates = []
    if container_id:
        try:
            candidates = [client.containers.get(container_id)]
        except Exception:
            candidates = []
    if not candidates:
        try:
            candidates = client.containers.list(
                filters={"label": f"smsly.service_id={service.id}"}
            )
        except Exception:
            candidates = []
    if not candidates:
        try:
            one = client.containers.get(service.name)
            candidates = [one]
        except Exception:
            candidates = []
    running = [c for c in candidates if getattr(c, "status", "") == "running"]
    if not running:
        return None
    # Prefer an exact id/name match when several share the label
    # (blue/green pairs); otherwise take the first running one.
    if container_id:
        for c in running:
            if c.id == container_id or getattr(c, "name", "") == container_id:
                return c
    return running[0]


def _fresh_env(service) -> dict:
    env_vars = {ev.key: ev.value for ev in service.env_vars.all()}
    try:
        from apps.deployments.services.mtls_integration import get_mtls_env_vars
        env_vars.update(get_mtls_env_vars(service) or {})
    except Exception as exc:
        logger.debug("mTLS env injection skipped for %s: %s", service.name, exc)
    return env_vars


def _container_networks(container) -> tuple[str, dict]:
    """Return (primary_network, {net: endpoint_config}) preserving aliases.

    Follows the AGENTS.md #15 pattern: docker-py only honors
    networking_config when ``network=`` is also passed and the config is
    a plain dict keyed by network name.
    """
    networks = ((container.attrs or {}).get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
    nets = {}
    for net_name, conf in networks.items():
        aliases = list((conf or {}).get("Aliases", []) or [])
        nets[net_name] = {"aliases": aliases}
    primary = None
    for net_name in nets:
        if net_name not in ("bridge", "host", "none"):
            primary = net_name
            break
    if primary is None and nets:
        primary = next(iter(nets))
    return primary, nets


def _container_volumes(container) -> dict:
    """Rebuild the docker-py ``volumes`` mapping from inspect data."""
    volumes = {}
    binds = (((container.attrs or {}).get("HostConfig", {}) or {}).get("Binds", []) or [])
    for item in binds:
        parts = str(item).split(":")
        if len(parts) >= 2:
            volumes[parts[0]] = {"bind": parts[1], "mode": parts[2] if len(parts) > 2 else "rw"}
    for mount in (container.attrs or {}).get("Mounts", []) or []:
        mtype = (mount or {}).get("Type", "")
        src, dst = (mount or {}).get("Source", ""), (mount or {}).get("Destination", "")
        if mtype in ("bind", "volume") and src and dst:
            volumes[src] = {"bind": dst, "mode": (mount.get("Mode", "") or "rw")}
        elif mtype not in ("", "bind", "volume"):
            raise ContainerRefreshError(
                f"Unsupported mount type '{mtype}' on {dst or '?'} — refusing to recreate"
            )
    return volumes


def _resource_kwargs(service) -> dict:
    try:
        cpus = float(getattr(service, "cpu_cores", 0) or 0)
    except (TypeError, ValueError):
        cpus = 0.0
    try:
        mem_mb = int(getattr(service, "memory_mb", 0) or 0)
    except (TypeError, ValueError):
        mem_mb = 0
    kwargs = {}
    if cpus > 0:
        kwargs["nano_cpus"] = int(cpus * 1e9)
    if mem_mb > 0:
        kwargs["mem_limit"] = f"{mem_mb}m"
        kwargs["memswap_limit"] = f"{mem_mb * 2}m"
    return kwargs


def build_refresh_plan(service, container) -> dict:
    """Describe what a recreate would do, without touching anything."""
    image = ""
    try:
        tags = (container.image.tags or []) if getattr(container, "image", None) else []
        image = tags[0] if tags else ((container.attrs or {}).get("Config", {}) or {}).get("Image", "")
    except Exception:
        image = ""
    primary, nets = _container_networks(container)
    env_vars = _fresh_env(service)
    return {
        "container_id": container.id[:12] if getattr(container, "id", "") else "",
        "container_name": getattr(container, "name", ""),
        "image": image,
        "env_keys": len(env_vars),
        "primary_network": primary,
        "networks": sorted(nets),
        "resources": _resource_kwargs(service),
    }


def _wait_running(container, timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            container.reload()
            if getattr(container, "status", "") == "running":
                return True
        except Exception:
            return False
        time.sleep(2)
    return False


def recreate_with_fresh_env(service, container_id=None, dry_run=False) -> dict:
    """Recreate the service's running container with fresh DB env/config.

    Returns {"ok": True, "container": name, "previous": backup_name, ...}.
    Raises ContainerRefreshError on any failure AFTER attempting rollback.
    """
    if getattr(service, "server_id", None):
        raise ContainerRefreshError("Remote services are not supported yet — redeploy from the dashboard")
    client = _docker_client()
    container = _resolve_target_container(service, client, container_id)
    if container is None:
        raise ContainerRefreshError("No running container found for this service")
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": build_refresh_plan(service, container)}

    name = container.name
    backup_name = f"{name}-prev"
    try:
        image_tags = (container.image.tags or []) if getattr(container, "image", None) else []
        image = image_tags[0] if image_tags else ((container.attrs or {}).get("Config", {}) or {}).get("Image", "")
        if not image:
            raise ContainerRefreshError("Could not determine the running image — refusing to recreate")
        labels = dict((container.attrs or {}).get("Config", {}).get("Labels", {}) or {})
        host_config = (container.attrs or {}).get("HostConfig", {}) or {}
        restart_policy = host_config.get("RestartPolicy") or {"Name": "unless-stopped"}
        runtime = host_config.get("Runtime") or None
        primary, nets = _container_networks(container)
        if not primary:
            raise ContainerRefreshError("Container is not attached to any network — refusing to recreate")
        volumes = _container_volumes(container)
        env_vars = _fresh_env(service)

        networking_config = {
            net_name: client.api.create_endpoint_config(aliases=info["aliases"])
            for net_name, info in nets.items()
        }
        create_kwargs = {
            "image": image,
            "name": name,
            "environment": env_vars,
            "network": primary,
            "networking_config": networking_config,
            "labels": labels,
            "volumes": volumes or None,
            "restart_policy": restart_policy,
            "detach": True,
        }
        if runtime:
            create_kwargs["runtime"] = runtime
        create_kwargs.update(_resource_kwargs(service))

        container.stop(timeout=15)
        try:
            client.containers.get(backup_name).remove(force=True)
        except Exception:
            pass
        container.rename(backup_name)
        try:
            new_container = client.containers.create(**create_kwargs)
            new_container.start()
        except Exception as exc:
            rollback_refresh(client, name, backup_name)
            raise ContainerRefreshError(f"Replacement failed to create/start: {exc}")
        if not _wait_running(new_container):
            rollback_refresh(client, name, backup_name)
            raise ContainerRefreshError("Replacement did not reach running state — rolled back")
        try:
            old = client.containers.get(backup_name)
            old.remove(force=True)
        except Exception as exc:
            logger.warning("Refreshed %s but could not remove backup %s: %s", name, backup_name, exc)
        return {
            "ok": True,
            "container": name,
            "container_id": (new_container.id or "")[:12],
            "previous": backup_name,
            "env_keys": len(env_vars),
        }
    except ContainerRefreshError:
        raise
    except Exception as exc:
        raise ContainerRefreshError(str(exc))


def rollback_refresh(client, name: str, backup_name: str) -> None:
    """Best-effort rollback helper used by the endpoint on late failure."""
    try:
        try:
            doomed = client.containers.get(name)
            doomed.remove(force=True)
        except Exception:
            pass
        prev = client.containers.get(backup_name)
        prev.rename(name)
        prev.start()
    except Exception as exc:
        logger.error("Container refresh rollback failed for %s: %s", name, exc)
        raise ContainerRefreshError(f"Recreate failed AND rollback failed: {exc}")
