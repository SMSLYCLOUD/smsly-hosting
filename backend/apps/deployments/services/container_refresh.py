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
  * remote-node services recreate on their home node via the node
    recreate API (same image, DB env merged over live env).
"""
import logging
import os
import time

logger = logging.getLogger(__name__)


class ContainerRefreshError(RuntimeError):
    """Fatal, user-facing recreation failure (rollback already attempted)."""


def _recreate_remote_with_fresh_env(
    service,
    dry_run: bool = False,
    include_stopped: bool = False,
) -> dict:
    """Same-image recreate on the service's node (no rebuild).

    Overrides are the decrypted DB rows (ciphertext-looking values
    skipped, mirroring the deploy pipeline); the node merges them over
    live container env, clones config, and rolls back on failure.
    """
    try:
        from apps.deployments.services.remote_orchestrator import RemoteOrchestrator
        from apps.deployments.services.caddy_manager.config_generation import (
            _resolve_effective_server,
        )
        server = _resolve_effective_server(service) or getattr(service, "server", None)
        if server is None or getattr(server, "is_primary", True):
            raise ContainerRefreshError("No remote home server for this service")
        overrides: dict[str, str] = {}
        for var in service.env_vars.all():
            key = (getattr(var, "key", "") or "").strip()
            if not key:
                continue
            try:
                value = var.value or ""
            except Exception:
                continue
            if isinstance(value, str) and value.startswith("gAAAAA") and len(value) > 50:
                continue
            overrides[key] = "" if value is None else str(value)
        try:
            # Same SOPS recipient the build pipeline injects (public).
            from apps.deployments.services.secrets_sops import ensure_age_keypair
            _pub, _ = ensure_age_keypair()
            if _pub.startswith("age1"):
                overrides.setdefault("SOPS_AGE_RECIPIENTS", _pub)
                overrides.setdefault("SOPS_AGE_PUBLIC_KEY", _pub)
        except Exception:
            pass
        from apps.core.crypto_daemon import apply_cryptd_remote_runtime_env
        try:
            apply_cryptd_remote_runtime_env(service, overrides, unlock_now=not dry_run)
        except Exception as exc:
            raise ContainerRefreshError(f"grid-cryptd refused remote apply-env: {exc}") from exc
        ref = (
            (getattr(service, "active_runtime_id", "") or "").strip()
            or (service.name or "").strip()
        )
        result = RemoteOrchestrator(server).recreate_remote_container(
            ref, overrides, dry_run=dry_run, include_stopped=include_stopped,
        )
        if (not result or not result.get("ok")) and ref != (service.name or "").strip():
            # Stale runtime IDs (old container long replaced) make the
            # node answer not-found — retry once by stable service name
            # before giving up. Never raises here; the caller decides.
            logger.warning(
                "Remote recreate by runtime id failed for %s; retrying by service name",
                service.name,
            )
            result = RemoteOrchestrator(server).recreate_remote_container(
                (service.name or "").strip(),
                overrides,
                dry_run=dry_run,
                include_stopped=include_stopped,
            )
        if not result or not result.get("ok"):
            raise ContainerRefreshError("Node recreate refused or unreachable")
        result["remote"] = getattr(server, "name", "")
        return result
    except ContainerRefreshError:
        raise
    except Exception as exc:
        raise ContainerRefreshError(f"Remote apply-env failed: {exc}")


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


def _resolve_target_container(
    service,
    client,
    container_id=None,
    *,
    include_stopped: bool = False,
):
    """Return the running (or optionally exited) container to refresh, or None."""
    candidates = []
    if container_id:
        try:
            candidates = [client.containers.get(container_id)]
        except Exception:
            candidates = []
    if not candidates:
        try:
            candidates = client.containers.list(
                all=include_stopped,
                filters={"label": f"smsly.service_id={service.id}"},
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
    pool = running
    if (
        not pool
        and include_stopped
        and str(getattr(service, "status", "") or "").upper() != "STOPPED"
    ):
        pool = [
            c for c in candidates
            if str(getattr(c, "status", "") or "").lower() in {"exited", "stopped", "created", "paused"}
            and not any(
                tag in str(getattr(c, "name", "") or "")
                for tag in ("-prev", "-green-", "-rollback-", "-promoting-")
            )
        ]
    if not pool:
        return None
    # Prefer an exact id/name match when several share the label
    # (blue/green pairs); otherwise take the first matching one.
    if container_id:
        for c in pool:
            if c.id == container_id or getattr(c, "name", "") == container_id:
                return c
    return pool[0]


def _live_container_env(container) -> dict:
    """Parse the running container's env (runtime truth, minus HOSTNAME)."""
    try:
        from .replica_parity import parse_env_list
        return parse_env_list(((container.attrs or {}).get("Config", {}) or {}).get("Env", []))
    except Exception:
        return {}


def _fresh_env(service, live_env=None, *, unlock_cryptd: bool = True) -> dict:
    """DB rows overlaid on live container env.

    Parity rule (2026-09-12 outage): the live env carries
    pipeline-derived keys (notably PORT) that DB rows alone lack.
    Operator edits in the DB still win — that is the point of apply-env.
    """
    db_rows = {
        ev.key: ev.value
        for ev in service.env_vars.all()
        if not (isinstance(ev.value, str) and ev.value.startswith("gAAAA") and len(ev.value) > 50)
    }
    try:
        from .replica_parity import merge_replica_env
        env_vars = merge_replica_env(live_env or {}, db_rows)
    except Exception:
        env_vars = dict(live_env or {})
        env_vars.update(db_rows)
    try:
        from apps.deployments.services.mtls_integration import get_mtls_env_vars
        env_vars.update(get_mtls_env_vars(service) or {})
    except Exception as exc:
        logger.debug("mTLS env injection skipped for %s: %s", service.name, exc)
    try:
        # Same SOPS recipient the build pipeline injects (public, safe).
        # setdefault: an operator-pinned value always wins.
        from apps.deployments.services.secrets_sops import ensure_age_keypair
        _pub, _ = ensure_age_keypair()
        if _pub.startswith("age1"):
            env_vars.setdefault("SOPS_AGE_RECIPIENTS", _pub)
            env_vars.setdefault("SOPS_AGE_PUBLIC_KEY", _pub)
    except Exception as exc:
        logger.debug("SOPS recipient injection skipped for %s: %s", service.name, exc)
    from apps.core.crypto_daemon import apply_cryptd_runtime_env
    apply_cryptd_runtime_env(service, env_vars, unlock_now=unlock_cryptd)
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


def _live_healthcheck(container) -> dict | None:
    """Extract Config.Healthcheck normalized for docker-py containers.create()."""
    hc = ((container.attrs or {}).get("Config", {}) or {}).get("Healthcheck")
    if not isinstance(hc, dict) or not hc.get("Test"):
        return None
    mapping = (
        ("Test", "test"),
        ("Interval", "interval"),
        ("Timeout", "timeout"),
        ("Retries", "retries"),
        ("StartPeriod", "start_period"),
        ("StartInterval", "start_interval"),
    )
    out = {dst: hc[src] for src, dst in mapping if src in hc and hc[src] is not None}
    return out or None


def _live_extra_hosts(container) -> list[str]:
    """Return the live container's extra_hosts entries (may be empty)."""
    try:
        hosts = ((container.attrs or {}).get("HostConfig", {}) or {}).get("ExtraHosts") or []
        return [str(h) for h in hosts if h]
    except Exception:
        return []


def _resolve_gvisor_extra_hosts(
    service,
    shared_nets,
    client,
    live=None,
    include_siblings: bool = True,
) -> list[str]:
    """Fresh hostname->IP mappings for addons (and project services) on shared networks.

    gVisor (runsc) sandboxes cannot reach Docker's embedded DNS proxy, so
    addon connections only work reliably via /etc/hosts entries.
    Live entries are kept; fresh resolutions win per hostname (stale entries
    would misroute). When ``service`` belongs to a ``project``:
    - With ``include_siblings=True``, running sibling ``Service`` containers in
      the project are also resolved to their live IP on ``shared_nets`` while
      skipping ``service`` itself and removing stale/dead sibling entries.
    - With ``include_siblings=False`` (when CoreDNS ``10.100.0.1`` is mounted
      for live sibling discovery), any stale sibling ``Service`` entries in
      ``live`` are stripped cleanly so static ``/etc/hosts`` never shadows DNS.
    Returns [] when nothing resolves — callers then omit extra_hosts entirely.
    Never raises (best-effort self-heal).
    """
    merged: dict[str, str] = {}
    if isinstance(live, dict):
        for host, ip in live.items():
            h = str(host or "").strip()
            v = str(ip or "").strip()
            if h and v:
                merged[h] = v
    else:
        for entry in live or []:
            host, sep, ip = str(entry).partition(":")
            if sep and host.strip() and ip.strip():
                merged[host.strip()] = ip.strip()

    own_name = (getattr(service, "name", "") or "").strip()
    if own_name:
        merged.pop(own_name, None)
        merged.pop(f"{own_name}.default.internal", None)

    addon_hosts: set[str] = set()
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
                # Include all active project addons (e.g. sibling RabbitMQ/Redis/DB) so gVisor containers reach them via /etc/hosts
                clauses |= Q(service__project__id=project_id)
            addon_qs = addon_qs.filter(clauses)
        from apps.addons.services.addon_provisioner import (
            backing_addon_container_name,
        )

        for addon in addon_qs:
            url = (getattr(addon, "connection_url", "") or "").strip()
            host = (_urlparse(url).hostname or "").strip() if url else ""
            if not host:
                continue
            addon_hosts.add(host)
            cname = backing_addon_container_name(addon)
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

    # Sibling project services: resolve active siblings or prune stale entries.
    try:
        svc_id = getattr(service, "id", None)
        project_id = getattr(service, "project_id", None)
        if project_id and client is not None:
            from apps.deployments.models.core import Service as _Service

            sibling_qs = _Service.objects.filter(project_id=project_id)
            if svc_id:
                sibling_qs = sibling_qs.exclude(id=svc_id)

            for sibling in sibling_qs:
                sib_name = (getattr(sibling, "name", "") or "").strip()
                if not sib_name or (own_name and sib_name == own_name):
                    continue
                if sib_name in addon_hosts:
                    continue

                if not include_siblings:
                    merged.pop(sib_name, None)
                    merged.pop(f"{sib_name}.default.internal", None)
                    continue

                refs: list[str] = []
                try:
                    deps_mgr = getattr(sibling, "deployments", None)
                    if deps_mgr is not None:
                        active_cid = (
                            deps_mgr.filter(status="ACTIVE")
                            .order_by("-created_at")
                            .values_list("container_id", flat=True)
                            .first()
                        )
                        if active_cid:
                            refs.append(str(active_cid).strip())
                except Exception:
                    pass
                if sib_name not in refs:
                    refs.append(sib_name)

                resolved_ip = ""
                for ref in refs:
                    if not ref:
                        continue
                    try:
                        candidate = client.containers.get(ref)
                        candidate.reload()
                        attrs = getattr(candidate, "attrs", None) or {}
                        state = str(
                            ((attrs.get("State") or {}).get("Status"))
                            or getattr(candidate, "status", "")
                            or ""
                        ).strip().lower()
                        if state and state != "running":
                            continue
                        cname = str(
                            getattr(candidate, "name", "") or attrs.get("Name") or ""
                        ).lstrip("/").strip()
                        if any(tag in cname.lower() for tag in ("-green-", "-promoting-", "-old-", "-prev")):
                            continue
                        snets = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
                        for net_name in shared_nets:
                            ip = ((snets.get(net_name) or {}).get("IPAddress") or "").strip()
                            if ip and sib_name != ip:
                                resolved_ip = ip
                                break
                        if resolved_ip:
                            break
                    except Exception:
                        continue

                if resolved_ip:
                    merged[sib_name] = resolved_ip
                    if f"{sib_name}.default.internal" in merged:
                        merged[f"{sib_name}.default.internal"] = resolved_ip
                else:
                    merged.pop(sib_name, None)
                    merged.pop(f"{sib_name}.default.internal", None)
    except Exception as exc:
        logger.debug("gVisor sibling extra_hosts resolution skipped: %s", exc)

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
    env_vars = _fresh_env(service, _live_container_env(container), unlock_cryptd=False)
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
                            force_default_runtime=False,
                            include_stopped=False) -> dict:
    """Recreate the service's running container with fresh config, without rebuilding.

    Returns {"ok": True, "container": name, "previous": backup_name, ...}.
    Raises ContainerRefreshError on any failure AFTER attempting rollback.

    :param force_default_runtime: drop back to the daemon default runtime
        (runc) instead of cloning the live one — the escape hatch out of
        a broken sandboxed runtime (e.g. gVisor DNS failure). Implies no
        extra_hosts (stale overrides would shadow healthy DNS).
    :param include_stopped: also allow recreating a Sablier-sleeping
        (exited/stopped) container when updating scale-to-zero labels.
    """
    if _is_remote_service(service):
        return _recreate_remote_with_fresh_env(
            service, dry_run=dry_run, include_stopped=include_stopped,
        )
    client = _docker_client()
    container = _resolve_target_container(
        service, client, container_id, include_stopped=include_stopped,
    )
    if container is None:
        raise ContainerRefreshError("No running container found for this service")
    if dry_run:
        return {"ok": True, "dry_run": True, "plan": build_refresh_plan(service, container)}

    name = container.name
    backup_name = f"{name}-prev"
    was_running = str(getattr(container, "status", "") or "").lower() == "running"
    try:
        image_tags = (container.image.tags or []) if getattr(container, "image", None) else []
        image = image_tags[0] if image_tags else ((container.attrs or {}).get("Config", {}) or {}).get("Image", "")
        if not image:
            raise ContainerRefreshError("Could not determine the running image — refusing to recreate")
        labels = dict((container.attrs or {}).get("Config", {}).get("Labels", {}) or {})
        try:
            from apps.deployments.services.traefik_labels import (
                service_supports_sablier,
                sync_sablier_labels_on_dict,
            )
            sync_sablier_labels_on_dict(labels, service)
        except Exception as exc:
            logger.debug("Sablier label sync skipped during refresh for %s: %s", service.name, exc)
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
        if "ReadonlyRootfs" in host_config:
            create_kwargs["read_only"] = bool(host_config.get("ReadonlyRootfs"))
        if host_config.get("Tmpfs"):
            create_kwargs["tmpfs"] = dict(host_config.get("Tmpfs"))
        if host_config.get("Ulimits"):
            create_kwargs["ulimits"] = list(host_config.get("Ulimits"))
        if force_default_runtime:
            # Escape hatch out of a broken sandboxed runtime: omit the key
            # so the daemon default (runc) applies, and drop extra_hosts —
            # stale hostname overrides would shadow the now-healthy DNS.
            create_kwargs.pop("runtime", None)
        from apps.core.crypto_daemon import needs_host_gateway
        if not force_default_runtime:
            from apps.deployments.services.container_runtime import (
                ensure_sandbox_resolv_conf,
                is_sandboxed_runtime,
                sandbox_dns_servers,
            )
            effective_rt = create_kwargs.get("runtime") or ""
            live_dns = host_config.get("Dns")
            if live_dns:
                create_kwargs["dns"] = list(live_dns)
            elif is_sandboxed_runtime(effective_rt):
                create_kwargs["dns"] = sandbox_dns_servers()
            _refresh_resolv_mounted = False
            if is_sandboxed_runtime(effective_rt):
                try:
                    _resolv_path = ensure_sandbox_resolv_conf(create_kwargs.get("dns"))
                    if _resolv_path and os.path.isfile(_resolv_path):
                        _vols = dict(create_kwargs.get("volumes") or {})
                        if not any(
                            isinstance(v, dict) and v.get("bind") == "/etc/resolv.conf"
                            for v in _vols.values()
                        ):
                            _vols[_resolv_path] = {"bind": "/etc/resolv.conf", "mode": "ro"}
                            create_kwargs["volumes"] = _vols
                        _refresh_resolv_mounted = True
                except Exception as exc:
                    logger.debug("Refresh sandbox resolv.conf mount skipped: %s", exc)
            if effective_rt == "runsc":
                # gVisor cannot reach Docker embedded DNS: refresh the /etc/hosts
                # compensation (addons resolved fresh; stale sibling entries pruned
                # when CoreDNS resolv.conf handles live discovery).
                hosts = _resolve_gvisor_extra_hosts(
                    service,
                    set(nets),
                    client,
                    live=_live_extra_hosts(container),
                    include_siblings=not _refresh_resolv_mounted,
                )
                if hosts:
                    create_kwargs["extra_hosts"] = hosts
        if needs_host_gateway(env_vars):
            hosts_list = list(create_kwargs.get("extra_hosts") or [])
            if not any(str(h).startswith("host.docker.internal:") for h in hosts_list):
                hosts_list.append("host.docker.internal:host-gateway")
            create_kwargs["extra_hosts"] = hosts_list
        live_hc = _live_healthcheck(container)
        if live_hc:
            create_kwargs["healthcheck"] = live_hc
        create_kwargs.update(_resource_kwargs(service))

        if was_running:
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
            rollback_refresh(
                client, name, backup_name,
                start_backup=was_running or not bool(getattr(service, "sablier_enabled", False)),
            )
            raise ContainerRefreshError(f"Replacement failed to create/start: {exc}")
        if not _wait_running(new_container):
            rollback_refresh(
                client, name, backup_name,
                start_backup=was_running or not bool(getattr(service, "sablier_enabled", False)),
            )
            raise ContainerRefreshError("Replacement did not reach running state — rolled back")
        try:
            old = client.containers.get(backup_name)
            old.remove(force=True)
        except Exception as exc:
            logger.warning("Refreshed %s but could not remove backup %s: %s", name, backup_name, exc)
        # Keep active deployment/service runtime pointers aligned with the recreated container.
        try:
            from apps.deployments.models import Deployment
            active_dep = (
                Deployment.objects.filter(service=service, status=Deployment.Status.ACTIVE)
                .order_by("-created_at")
                .first()
            )
            if active_dep is not None and new_container.id:
                active_dep.container_id = new_container.id
                active_dep.save(update_fields=["container_id", "updated_at"])
            if getattr(service, "active_runtime_id", "") and new_container.id:
                service.active_runtime_id = new_container.id[:12]
                service.save(update_fields=["active_runtime_id", "updated_at"])
        except Exception:
            pass
        try:
            if (
                bool(getattr(service, "sablier_enabled", False))
                and service_supports_sablier(service)[0]
            ):
                from apps.deployments.services.internal_wake import ensure_internal_wake_alias
                for net_name in nets:
                    if net_name and net_name != "bridge":
                        ensure_internal_wake_alias(service.name, net_name, client=client)
        except Exception:
            pass
        try:
            from apps.deployments.services.mesh_dns import sync_dns_now
            sync_dns_now(client=client)
        except Exception:
            pass
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


def rollback_refresh(client, name: str, backup_name: str, *, start_backup: bool = True) -> None:
    """Best-effort rollback helper used by the endpoint on late failure."""
    try:
        try:
            doomed = client.containers.get(name)
            doomed.remove(force=True)
        except Exception:
            pass
        prev = client.containers.get(backup_name)
        prev.rename(name)
        if start_backup:
            prev.start()
        try:
            from apps.deployments.services.mesh_dns import sync_dns_now
            sync_dns_now(client=client)
        except Exception:
            pass
    except Exception as exc:
        logger.error("Container refresh rollback failed for %s: %s", name, exc)
        raise ContainerRefreshError(f"Recreate failed AND rollback failed: {exc}")
