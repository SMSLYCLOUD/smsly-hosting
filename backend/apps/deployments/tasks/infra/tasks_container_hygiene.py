"""Container hygiene beat tasks.

* restart-loop watchdog — a container crash-looping (like the redis addon
  that hit 1,875 restarts unnoticed) pages within minutes instead of hours.
* orphan addon GC — codifies the alias-aware sweep: any smsly-addon-*
  container without a live DB record, or shadowing a canonical alias, is
  removed. Ran manually twice during the 2026-08 incident; now automatic.
* gVisor hosts reconciler — runsc sandboxes cannot reach Docker's embedded
  DNS, so they depend on static /etc/hosts entries injected at (re)create
  time. When a backing container is recreated (new IP) the entries go
  stale and the service loses its database with zero log signal. This
  sweeps runsc containers hourly and recreates the drifted ones via the
  rollback-safe container_refresh path.
"""
import logging
import subprocess

from celery import shared_task

from apps.deployments.constants import (
    TASK_TIME_LIMIT_QUICK,
    TASK_TIME_LIMIT_STANDARD,
)

logger = logging.getLogger(__name__)

RESTART_LOOP_MIN_COUNT = 10
RESTARTING_GRACE_SECONDS = 900  # 15 min before flagging


def _sh(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


@shared_task(
    name="apps.deployments.tasks.container_restart_loop_watchdog_task",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def container_restart_loop_watchdog_task():
    """Flag containers stuck in restart loops.

    Criteria (either):
      * RestartCount >= RESTART_LOOP_MIN_COUNT
      * Docker status 'Restarting' persistently (we rely on RestartCount for
        persistence across the 5-minute beat interval)
    Returns findings; logs at ERROR so log-based alerting can page.
    """
    out = _sh(['docker', 'ps', '-a', '--format', '{{.Names}}\t{{.Status}}'])
    findings = []
    for line in (out.stdout or '').splitlines():
        name, _, status = line.partition('\t')
        if not name:
            continue
        insp = _sh(['docker', 'inspect', name,
                    '--format', '{{.RestartCount}}\t{{.State.Status}}\t{{.State.ExitCode}}'])
        if insp.returncode != 0:
            continue
        try:
            restarts_s, state_s, exit_s = insp.stdout.strip().split('\t')
            restarts = int(restarts_s)
        except Exception:
            continue
        loop = restarts >= RESTART_LOOP_MIN_COUNT or (
            state_s == 'restarting' and restarts >= 3)
        if loop:
            findings.append({
                'container': name, 'restarts': restarts,
                'state': state_s, 'last_exit': exit_s,
            })
    if findings:
        logger.error(
            "RESTART-LOOP WATCHDOG: %d container(s) crash-looping: %s",
            len(findings), findings,
        )
    return {"status": "ok", "findings": findings}


@shared_task(
    name="apps.deployments.tasks.orphan_addon_gc_task",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def orphan_addon_gc_task(dry_run: bool = False):
    """Remove addon containers that no DB record backs.

    Safety rules (mirror of the manual incident sweep):
      * Only touches names starting with 'smsly-addon-'
      * A container is kept iff its name matches an ACTIVE addon record
        (canonical keeper per alias); everything else under the prefix is
        removed — duplicates shadowing a healthy alias included, since DNS
        round-robins between live and dead instances.
    """
    from urllib.parse import urlparse
    from apps.deployments.models.addons import Addon

    keep = set()
    for a in Addon.objects.exclude(status='DELETED'):
        keep.add(f"smsly-addon-{a.addon_type.lower()}-{a.id}")

    out = _sh(['docker', 'ps', '-a', '--format', '{{.Names}}\t{{.Status}}'])
    removed, skipped = [], []
    for line in (out.stdout or '').splitlines():
        name, _, status = line.partition('\t')
        if not name.startswith('smsly-addon-'):
            continue
        if name in keep:
            continue
        if dry_run:
            skipped.append(name)
            continue
        res = _sh(['docker', 'rm', '-f', name], timeout=90)
        if res.returncode == 0:
            removed.append(name)
            logger.info("orphan_addon_gc: removed %s [%s]", name, status.strip()[:30])
        else:
            skipped.append(name)
    if removed:
        logger.warning("orphan_addon_gc: removed %d orphan container(s): %s",
                       len(removed), removed)
    return {"status": "ok", "removed": removed, "skipped": skipped}


# Cap recreates per run: a shared-server IP change drifts EVERY runsc
# service at once, and each recreate costs seconds of downtime. Heal a
# few per hour and let the next beats finish the queue.
RECONCILE_MAX_RECREATES = 3


def _hosts_drift(live: list[str], expected: dict[str, str]) -> dict[str, str]:
    """Return {hostname: current_ip} entries that are missing or stale.

    Pure function (unit-tested): live is ["host:ip", ...] from the
    container's ExtraHosts, expected maps hostname -> live backend IP.
    Hostnames with no expected IP (backend down) are never drift — we'd
    churn containers toward an equally-broken state.
    """
    drift = {}
    live_map = {}
    for entry in live or []:
        host, sep, ip = str(entry).partition(":")
        if sep and host.strip() and ip.strip():
            live_map[host.strip()] = ip.strip()
    for host, ip in (expected or {}).items():
        if not host or not ip:
            continue
        if live_map.get(host) != ip:
            drift[host] = ip
    return drift


def _backing_container_name(addon) -> str:
    """Dedicated container name, or the shared server / tenant pooler."""
    atype = str(getattr(addon, "addon_type", "") or "").lower()
    if (atype == "postgres"
            and str(getattr(addon, "provision_mode", "") or "") == "shared"):
        if getattr(addon, "pooler_routed", False):
            try:
                from apps.addons.services import tenant_pooler as _pooler
                name = _pooler.tenants_container_name()
                if name:
                    return name
            except Exception:
                pass
        try:
            from apps.addons.services.shared_postgres import (
                SHARED_CONTAINER as _SHARED,
            )
            return _SHARED
        except Exception:
            return "smsly-shared-postgres"
    return f"smsly-addon-{atype}-{getattr(addon, 'id', '')}"


@shared_task(
    name="apps.deployments.tasks.reconcile_gvisor_hosts_task",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def reconcile_gvisor_hosts_task(dry_run: bool = False):
    """Recreate runsc containers whose /etc/hosts addon entries drifted.

    Detection per running runsc container owned by a Service row:
    for each ACTIVE addon with a connection URL, resolve the backing
    container's CURRENT IP on a network the service shares, and compare
    against the container's ExtraHosts. Missing or stale entries mean
    the service cannot resolve its database (gVisor has no embedded
    DNS fallback) — recreate via the rollback-safe container_refresh
    path, which re-injects fresh mappings.

    Never touches: non-runsc containers, services without rows, addons
    without URLs, backends that are down (no expected IP), remote-node
    services (recreate refuses them — logged and skipped).
    """
    import json
    from urllib.parse import urlparse as _urlparse

    from apps.deployments.models.addons import Addon
    from apps.deployments.models.service import Service

    out = _sh(['docker', 'ps', '--format', '{{.Names}}'])
    recreated, skipped, checked = [], [], 0
    for name in (out.stdout or '').splitlines():
        name = name.strip()
        if not name:
            continue
        insp = _sh(['docker', 'inspect', name, '--format',
                    '{{.HostConfig.Runtime}}\t{{.State.Status}}\t'
                    '{{json .Config.Labels}}\t{{json .HostConfig.ExtraHosts}}\t'
                    '{{json .NetworkSettings.Networks}}'])
        if insp.returncode != 0:
            continue
        try:
            runtime, state, labels_s, hosts_s, nets_s = insp.stdout.strip().split('\t')
            labels = json.loads(labels_s or '{}')
            live_hosts = json.loads(hosts_s or '[]')
            nets = json.loads(nets_s or '{}')
        except Exception:
            continue
        if runtime != 'runsc' or state != 'running':
            continue
        service_id = (labels or {}).get('smsly.service_id')
        if not service_id:
            continue
        try:
            service = Service.objects.get(id=service_id)
        except Exception:
            skipped.append({"container": name, "reason": "no-service-row"})
            continue
        checked += 1
        service_nets = set((nets or {}).keys())
        expected: dict[str, str] = {}
        for addon in Addon.objects.filter(service=service, status='ACTIVE'):
            url = (getattr(addon, 'connection_url', '') or '').strip()
            if not url:
                continue
            try:
                host = (_urlparse(url).hostname or '').strip().lower()
            except Exception:
                continue
            if not host:
                continue
            back = _sh(['docker', 'inspect', _backing_container_name(addon),
                        '--format', '{{json .NetworkSettings.Networks}}'])
            if back.returncode != 0:
                continue
            try:
                back_nets = json.loads(back.stdout.strip() or '{}')
            except Exception:
                continue
            for net_name in service_nets:
                ip = ((back_nets.get(net_name) or {}).get('IPAddress') or '').strip()
                if ip:
                    expected[host] = ip
                    break
        drift = _hosts_drift(live_hosts, expected)
        if not drift:
            continue
        if dry_run or len(recreated) >= RECONCILE_MAX_RECREATES:
            skipped.append({"container": name, "drift": drift,
                            "reason": "dry-run" if dry_run else "cap-reached"})
            continue
        try:
            from apps.deployments.services.container_refresh import (
                recreate_with_fresh_env,
            )
            res = recreate_with_fresh_env(service)
            recreated.append({"container": name, "drift": drift,
                              "replacement": res.get("container")})
            logger.warning("gvisor-hosts-reconcile: recreated %s (drift=%s)",
                           name, drift)
        except Exception as exc:
            skipped.append({"container": name, "drift": drift,
                            "reason": f"recreate-failed: {exc}"})
    if recreated:
        logger.warning("gvisor-hosts-reconcile: recreated %d container(s): %s",
                       len(recreated), [r["container"] for r in recreated])
    return {"status": "ok", "checked": checked, "recreated": recreated,
            "skipped": skipped}
