"""Recreate a running service container with fresh config, without rebuilding.

Linux processes snapshot their environment at exec: editing env vars in
the DB never reaches an already-running container, and ``docker restart``
keeps the OLD environment (restart != recreate). The only instant path
is stop + recreate from the SAME image with fresh env — seconds of
downtime for a process boot, no build, no registry pull.

Safety:
  * the old container is renamed (not removed) until the replacement
    reports running; any failure rolls back (rename back + start).
  * networks/aliases, labels, mounts, restart policy, and resource
    limits are cloned from the live container, so routing (Traefik
    labels, DNS aliases) and isolation survive intact.
  * runtime is preserved by default; pass force_default_runtime=True
    to drop back to the daemon default (runc).
  * gVisor (runsc) sandboxes cannot reach Docker's embedded DNS proxy,
    so replacements running under runsc get fresh addon hostname->IP
    mappings injected via extra_hosts (live entries kept, fresh
    resolutions win). Non-runsc replacements deliberately carry NO
    extra_hosts — stale overrides would shadow healthy DNS because
    nsswitch consults files before dns.
  * resource limits come from the Service row (converging drift), env
    from EnvironmentVariable rows + mTLS injection (mirroring spawn).
  * remote-node services are refused — run where the code is current.
"""
import logging
import time

logger = logging.getLogger(__name__)


class ContainerRefreshError(RuntimeError):
    """Fatal, user-facing recreation failure (rollback already attempted)."""


def _is_remote_service(service) -> bool:
    """True only when the service provably runs on a different node.

    ``Service.server`` is "where hosted" — it is also set for LOCAL
    services (primary node record), so a bare ``server_id`` check
    wrongly refuses apply-env on local services. Mirror the deploy
    path locality semantics instead (primary / controller-IP servers
    are local). Fail closed (remote) when locality cannot be proven.
    """
    try:
        server = getattr(service, "server", None)
        # Both must be set: server_id None means "no host assignment"
        # (also keeps MagicMock-based unit tests, which auto-create a
        # truthy .server, on the local path when server_id is None).
        if server is not None and getattr(service, "server_id", None) is not None:
            from apps.deployments.models import PlatformConfig
            from apps.deployments.tasks.deploy.provider import (
                _is_local_deployment_server,
            )
            if not _is_local_deployment_server(server, PlatformConfig.load()):
                return True
        if str(getattr(service, "active_target_type", "") or "").lower() in (
            "remote", "lite_agent",
        ):
            return bool(getattr(service, "active_host_ip", None))
        return False
    except Exception:
        return True


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


def _live_container_env(container) -> dict:
    """Parse the running container's env (runtime truth, minus HOSTNAME)."""
    try:
        from .replica_parity import parse_env_list
        return parse_env_list(((container.attrs or {}).get("Config", {}) or {}).get("Env", []))
    except Exception:
        return {}


def _fresh_env(service, live_env=None) -> dict:
    """DB rows overlaid on live container env.

    Parity rule (2026-09-12 outage): the live env carries
    pipeline-derived keys (notably PORT) that DB rows alone lack.
    Operator edits in the DB still win — that is the point of apply-env.
    """
    try:
        from .replica_parity import merge_replica_env
        env_vars = merge_replica_env(live_env or {}, {ev.key: ev.value for ev in service.env_vars.all()})
    except Exception:
        env_vars = dict(live_env or {})
        env_vars.update({ev.key: ev.value for ev in service.env_vars.all()})
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

    def add_mount(source, destination, mode):
        for old_source, old_config in list(volumes.items()):
            if old_config.get("bind") == destination:
                del volumes[old_source]
        volumes[source] = {"bind": destination, "mode": mode}

    binds = (((container.attrs or {}).get("HostConfig", {}) or {}).get("Binds", []) or [])
    for item in binds:
        parts = str(item).split(":")
        if len(parts) >= 2:
            add_mount(parts[0], parts[1], parts[2] if len(parts) > 2 else "rw")
    for mount in (container.attrs or {}).get("Mounts", []) or []:
        mtype = (mount or {}).get("Type", "")
        src, dst = (mount or {}).get("Source", ""), (mount or {}).get("Destination", "")
        if mtype in ("bind", "volume") and src and dst:
            add_mount(src, dst, (mount.get("Mode", "") or "rw"))
        elif mtype not in ("", "bind", "volume"):
            raise ContainerRefreshError(
                f"Unsupported mount type '{mtype}' on {dst or '?'} — refusing to recreate"
            )
    return volumes


def _live_extra_hosts(container) -> list[str]:
    """Return the live container's extra_hosts entries (may be empty)."""
    try:
        hosts = ((container.attrs or {}).get("HostConfig", {}) or {}).get("ExtraHosts") or []
        return [str(h) for h in hosts if h]
    except Exception:
        return []


def _resolve_gvisor_extra_hosts(service, shared_nets, client, live=None) -> list[str]:
    """Fresh hostname->IP mappings for addons on the service's networks.

    gVisor (runsc) sandboxes cannot reach Docker's embedded DNS proxy, so
    hostname-based addon connections only work via /etc/hosts entries.
    Live entries are kept; fresh resolutions win per hostname (addon IPs
    change across addon recreates, so a preserved stale entry would
    misroute). Returns [] when nothing resolves — callers then omit
    extra_hosts entirely. Never raises (best-effort self-heal).
    """
    merged: dict[str, str] = {}
    for entry in live or []:
        host, sep, ip = str(entry).partition(":")
        if sep and host.strip() and ip.strip():
            merged[host.strip()] = ip.strip()
    try:
        from urllib.parse import urlparse as _urlparse

        from django.db.models import Q

        from apps.deployments.models.addons import Addon as _Addon

        svc_id = getattr(service, "id", None)
        project_id = getattr(service, "project_id", None)
        addon_qs = _Addon.objects.filter(status="ACTIVE")
        if svc_id or project_id:
            clauses = Q()
            if svc_id:
                clauses |= Q(service__id=svc_id)
            if project_id:
                clauses |= Q(service__project__id=project_id, name__endswith="-shared")
            addon_qs = addon_qs.filter(clauses)
        for addon in addon_qs:
            url = (getattr(addon, "connection_url", "") or "").strip()
            host = (_urlparse(url).hostname or "").strip() if url else ""
            if not host:
                continue
            atype = str(getattr(addon, "addon_type", "") or "").lower()
            cname = f"smsly-addon-{atype}-{getattr(addon, 'id', '')}"
            if (atype == "postgres"
                    and str(getattr(addon, "provision_mode", "") or "") == "shared"):
                # Logical database — there is intentionally no per-addon
                # container. Resolve the shared server (or tenant pooler)
                # instead, mirroring _shared_backend_container in
                # tasks/deploy/addons.py (kept local to avoid a
                # services->tasks import cycle).
                cname = "smsly-shared-postgres"
                try:
                    from apps.addons.services.shared_postgres import (
                        SHARED_CONTAINER as _SHARED,
                    )
                    cname = _SHARED
                except Exception:
                    pass
                if getattr(addon, "pooler_routed", False):
                    try:
                        from apps.addons.services import tenant_pooler as _pooler
                        _pname = _pooler.tenants_container_name()
                        if _pname:
                            cname = _pname
                    except Exception:
                        pass
            try:
                candidate = client.containers.get(cname)
                candidate.reload()
                anets = (candidate.attrs.get("NetworkSettings") or {}).get("Networks") or {}
                for net_name in shared_nets:
                    ip = (anets.get(net_name) or {}).get("IPAddress", "")
                    if ip and host != ip:
                        merged[host] = ip
                        break
            except Exception:
                continue
    except Exception as exc:
        logger.debug("gVisor extra_hosts resolution skipped: %s", exc)
    return [f"{host}:{ip}" for host, ip in merged.items()]


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
    env_vars = _fresh_env(service, _live_container_env(container))
    try:
        live_runtime = ((container.attrs or {}).get("HostConfig", {}) or {}).get("Runtime") or None
    except Exception:
        live_runtime = None
    return {
        "container_id": container.id[:12] if getattr(container, "id", "") else "",
        "container_name": getattr(container, "name", ""),
        "image": image,
        "env_keys": len(env_vars),
        "primary_network": primary,
        "networks": sorted(nets),
        "resources": _resource_kwargs(service),
        "runtime": live_runtime,
        "extra_hosts": _live_extra_hosts(container),
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


def recreate_with_fresh_env(service, container_id=None, dry_run=False,
                            force_default_runtime=False) -> dict:
    """Recreate the service's running container with fresh config, without rebuilding.

    Returns {"ok": True, "container": name, "previous": backup_name, ...}.
    Raises ContainerRefreshError on any failure AFTER attempting rollback.

    :param force_default_runtime: drop back to the daemon default runtime
        (runc) instead of cloning the live one — the escape hatch out of
        a broken sandboxed runtime (e.g. gVisor DNS failure). Implies no
        extra_hosts (stale overrides would shadow healthy DNS).
    """
    if _is_remote_service(service):
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
        env_vars = _fresh_env(service, _live_container_env(container))
        try:
            from apps.deployments.services.mtls_integration import (
                get_mtls_docker_run_volumes,
                get_mtls_env_vars,
                get_mtls_labels,
                merge_docker_volumes,
            )
            labels.update(get_mtls_labels(service))
            env_vars.update(get_mtls_env_vars(service))
            volumes = merge_docker_volumes(volumes, get_mtls_docker_run_volumes(service))
        except Exception as exc:
            logger.warning("mTLS refresh integration skipped for %s: %s", service.name, exc)

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
        if force_default_runtime:
            # Escape hatch out of a broken sandboxed runtime: omit the key
            # so the daemon default (runc) applies, and drop extra_hosts —
            # stale hostname overrides would shadow the now-healthy DNS.
            create_kwargs.pop("runtime", None)
        elif (create_kwargs.get("runtime") or "") == "runsc":
            # gVisor cannot reach Docker embedded DNS: refresh the /etc/hosts
            # compensation (live entries kept, fresh resolutions win).
            # Recreate previously dropped this, stranding runsc containers
            # with zero name resolution (2026-09-14 platform-api incident).
            hosts = _resolve_gvisor_extra_hosts(
                service, set(nets), client,
                live=_live_extra_hosts(container),
            )
            if hosts:
                create_kwargs["extra_hosts"] = hosts
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
