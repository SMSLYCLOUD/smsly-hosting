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
import re
import subprocess

from celery import shared_task

from apps.deployments.constants import (
    TASK_TIME_LIMIT_QUICK,
    TASK_TIME_LIMIT_STANDARD,
)
from apps.deployments.services.power import (
    is_platform_or_infra_container,
    quarantine_crash_loop_service,
)

logger = logging.getLogger(__name__)

RESTART_LOOP_MIN_COUNT = 10
RESTART_LOOP_DELTA_MIN = 3
RESTART_LOOP_RESTARTING_MIN = 5
CHILD_CRASH_UNHEALTHY_STREAK = 10
CHILD_CRASH_MIN_MATCHES = 3
RESTARTING_GRACE_SECONDS = 900  # 15 min before flagging

_CHILD_CRASH_RE = re.compile(
    r"Child process \[\d+\] died"
    r"|Worker \(pid:\d+\) exited with code [1-9]"
    r"|Worker failed to boot"
    r"|RuntimeError:\s*FATAL:"
    r"|Refusing to boot"
    r"|ModuleNotFoundError:"
    r"|ImportError:"
    r"|Address already in use"
)


def detect_child_crash_loop(logs: str) -> tuple[bool, str]:
    """Return ``(True, sample_line)`` when child workers repeatedly die inside a live PID 1."""
    if not logs:
        return False, ""
    matches = 0
    sample = ""
    for line in logs.splitlines():
        if _CHILD_CRASH_RE.search(line):
            matches += 1
            if not sample:
                sample = line.strip()[:160]
    return (matches >= CHILD_CRASH_MIN_MATCHES), (sample if matches >= CHILD_CRASH_MIN_MATCHES else "")


def _sh(args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _resolve_tenant_service(cid: str, container_name: str):
    """Best-effort lookup of the tenant Service owning an active container."""
    from apps.deployments.models import Deployment, Service

    svc = Service.objects.filter(name=container_name).first()
    if svc is not None:
        return svc
    dep = (
        Deployment.objects.filter(
            container_id__startswith=cid[:12],
            status=Deployment.Status.ACTIVE,
        )
        .select_related("service")
        .first()
    )
    return dep.service if dep else None


@shared_task(
    name="apps.deployments.tasks.container_restart_loop_watchdog_task",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def container_restart_loop_watchdog_task():
    """Detect active crash-looping containers (Docker restarts OR child-worker
    death loops), quarantine offending tenant containers, and alert admins.
    """
    from django.core.cache import cache

    out = _sh(['docker', 'ps', '-q'])
    ids = [cid.strip() for cid in (out.stdout or '').splitlines() if cid.strip()]
    findings = []
    if ids:
        insp = _sh([
            'docker', 'inspect',
            '--format',
            '{{.ID}}\t{{.Name}}\t{{.RestartCount}}\t{{.State.Status}}\t{{.State.ExitCode}}'
            '\t{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
            '\t{{if .State.Health}}{{.State.Health.FailingStreak}}{{else}}0{{end}}'
            '\t{{.HostConfig.RestartPolicy.Name}}',
            *ids,
        ])
        for line in (insp.stdout or '').splitlines():
            parts = line.strip().split('\t')
            if len(parts) < 8:
                continue
            cid, raw_name, restarts_s, state_s, exit_s, health_s, streak_s, _rp = parts[:8]
            name = raw_name.lstrip('/')
            try:
                restarts = int(restarts_s)
            except Exception:
                continue
            try:
                failing_streak = int(streak_s or 0)
            except Exception:
                failing_streak = 0

            cache_key = f"watchdog:restart_prev:{cid}"
            prev = cache.get(cache_key)
            cache.set(cache_key, restarts, timeout=3600)

            if health_s == "healthy":
                continue

            loop_reason = ""
            is_container_loop = (
                (state_s == "restarting" and restarts >= RESTART_LOOP_RESTARTING_MIN)
                or (
                    restarts >= RESTART_LOOP_MIN_COUNT
                    and prev is not None
                    and (restarts - prev) >= RESTART_LOOP_DELTA_MIN
                )
            )
            if is_container_loop:
                loop_reason = f"restart_loop (restarts={restarts}, state={state_s}, exit={exit_s})"
            elif (
                state_s == "running"
                and health_s == "unhealthy"
                and failing_streak >= CHILD_CRASH_UNHEALTHY_STREAK
                and not is_platform_or_infra_container(name)
            ):
                log_res = _sh(["docker", "logs", "--tail", "60", "--since", "3m", cid], timeout=10)
                combined = ((log_res.stdout or "") + "\n" + (log_res.stderr or "")).strip()
                child_loop, sample = detect_child_crash_loop(combined)
                if child_loop:
                    loop_reason = f"child_crash_loop (streak={failing_streak}): {sample}"

            if not loop_reason:
                continue

            quarantined = False
            if not is_platform_or_infra_container(name):
                try:
                    svc = _resolve_tenant_service(cid, name)
                    if svc is not None:
                        qres = quarantine_crash_loop_service(
                            svc,
                            name,
                            reason=loop_reason,
                            exit_code=exit_s,
                        )
                        quarantined = bool(qres.get("quarantined"))
                except Exception as exc:
                    logger.warning("Quarantine failed for %s: %s", name, exc)

            findings.append({
                'container': name,
                'restarts': restarts,
                'state': state_s,
                'last_exit': exit_s,
                'reason': loop_reason,
                'quarantined': quarantined,
            })
    if findings:
        logger.error(
            "RESTART-LOOP WATCHDOG: %d container(s) crash-looping: %s",
            len(findings), findings,
        )
        try:
            from apps.deployments.utils import log_event
            log_event(
                action="CONTAINER_RESTART_LOOP_DETECTED",
                target="platform:containers",
                actor="system",
                metadata={"count": len(findings), "findings": findings[:20]},
            )
            if cache.add("watchdog:restart_loop_alert_cooldown", 1, timeout=3600):
                from django.contrib.auth import get_user_model
                from apps.notifications.tasks import dispatch_notification
                User = get_user_model()
                summary = ", ".join(
                    f"{f['container']} ({f['restarts']} restarts, exit={f['last_exit']}, quarantined={f['quarantined']})"
                    for f in findings[:10]
                )
                for admin in User.objects.filter(is_superuser=True).only("id"):
                    dispatch_notification.delay(
                        event_type="health_alert",
                        user_id=admin.id,
                        title=f"CRITICAL: {len(findings)} Container(s) in Restart Loop",
                        message=f"Container restart-loop watchdog detected crash-looping containers: {summary}",
                        metadata={"findings": findings[:10]},
                    )
        except Exception as exc:
            logger.debug("Restart-loop alert dispatch skipped: %s", exc)
    return {
        "status": "ok",
        "findings": findings,
        "quarantined": [f["container"] for f in findings if f.get("quarantined")],
    }


@shared_task(
    name="apps.deployments.tasks.orphan_addon_gc_task",
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
)
def orphan_addon_gc_task(dry_run: bool = False):
    """Remove addon containers that no DB record backs.

    MASTER ONLY — returns immediately on nodes/lite-agents. Addon rows
    live in the master database; a node worker sees an empty table, so
    without this guard the hourly beat deletes every node addon
    container within an hour of provisioning (2026-10-04: marketer's
    node postgres/redis/rabbitmq vanished hourly while volumes and
    rows stayed intact).

    Safety rules (mirror of the manual incident sweep):
      * Only touches names starting with 'smsly-addon-'
      * A container is kept iff its name matches an ACTIVE addon record
        (canonical keeper per alias); everything else under the prefix is
        removed — duplicates shadowing a healthy alias included, since DNS
        round-robins between live and dead instances.
    """
    import os as _os
    # Node workers see an empty addon table (rows live on master): either
    # node identity var proves node-hood. Check BOTH — 2026-10-09: a
    # full-stack node's backend had SMSLY_NODE_ID unset and its beat
    # deleted freshly-provisioned node addon containers within the hour.
    _node_id = (_os.environ.get('SMSLY_NODE_ID', '') or '').strip()
    _node_queue = (_os.environ.get('SMSLY_NODE_QUEUE', '') or '').strip()
    if _node_id or _node_queue:
        logger.debug("orphan_addon_gc: skipping on node worker (no addon rows here)")
        return {"status": "skipped", "reason": "node-worker"}
    from urllib.parse import urlparse
    from apps.deployments.models.addons import Addon

    keep = set()
    for a in Addon.objects.exclude(status='DELETED'):
        keep.add(f"smsly-addon-{a.addon_type.lower()}-{a.id}")
    # Shared per-service CLI containers (smsly-addon-cli-<service-id>)
    # are backed by N addon rows, not one — keep them while any
    # non-deleted CLI addon row of the service remains (2026-10-02: the
    # GC removed a live shared container because no single row matched
    # its name; 2026-10-08: it removed one again because the only row
    # was BACKEND_MISSING — a row awaiting recovery still backs its
    # container, only DELETED rows release it).
    try:
        from apps.addons.services import cli_addons as _cli
        _cli_rows = Addon.objects.exclude(
            status=Addon.Status.DELETED,
        ).filter(
            addon_type__in=sorted(_cli.CLI_ADDON_TYPES),
        ).select_related('service')
        _seen_services: set[str] = set()
        for _row in _cli_rows:
            _sid = str(getattr(getattr(_row, 'service', None), 'id', ''))
            if _sid and _sid not in _seen_services:
                _seen_services.add(_sid)
                keep.add(_cli.shared_container_name(_row.service))
    except Exception as exc:
        logger.debug("CLI shared keep-set skipped: %s", exc)

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
    from apps.addons.services.addon_provisioner import backing_addon_container_name

    return backing_addon_container_name(addon)


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
        from django.db.models import Q
        project_id = getattr(service, 'project_id', None)
        addon_filter = Q(service=service)
        if project_id:
            addon_filter |= Q(service__project_id=project_id)
        for addon in Addon.objects.filter(status='ACTIVE').filter(addon_filter):
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
        # Check sibling project Service entries present in ExtraHosts so stale
        # entries from prior blue-green swaps never strand containers on dead IPs.
        live_map: dict[str, str] = {}
        for entry in live_hosts or []:
            h_k, sep, h_v = str(entry).partition(":")
            if sep and h_k.strip() and h_v.strip():
                live_map[h_k.strip()] = h_v.strip()

        stale_sibling_hosts: dict[str, str] = {}
        if project_id and live_map:
            try:
                siblings = Service.objects.filter(project_id=project_id).exclude(id=service.id)
            except Exception:
                siblings = ()
            for sibling in siblings:
                sib_name = (getattr(sibling, 'name', '') or '').strip()
                if not sib_name:
                    continue
                present_keys = [
                    k for k in (sib_name, f"{sib_name}.default.internal") if k in live_map
                ]
                if not present_keys:
                    continue
                refs: list[str] = []
                try:
                    deps_mgr = getattr(sibling, 'deployments', None)
                    if deps_mgr is not None:
                        active_cid = (
                            deps_mgr.filter(status='ACTIVE')
                            .order_by('-created_at')
                            .values_list('container_id', flat=True)
                            .first()
                        )
                        if active_cid:
                            refs.append(str(active_cid).strip())
                except Exception:
                    pass
                if sib_name not in refs:
                    refs.append(sib_name)
                sib_ip = ''
                for ref in refs:
                    sib_insp = _sh([
                        'docker', 'inspect', ref, '--format',
                        '{{.State.Status}}\t{{json .NetworkSettings.Networks}}',
                    ])
                    if sib_insp.returncode != 0:
                        continue
                    try:
                        sib_state, sib_nets_s = sib_insp.stdout.strip().split('\t')
                        if sib_state.strip().lower() == 'running':
                            sib_nets = json.loads(sib_nets_s or '{}')
                            for net_name in service_nets:
                                ip = ((sib_nets.get(net_name) or {}).get('IPAddress') or '').strip()
                                if ip:
                                    sib_ip = ip
                                    break
                    except Exception:
                        sib_ip = ''
                    if sib_ip:
                        break
                for host_key in present_keys:
                    if sib_ip:
                        expected[host_key] = sib_ip
                    else:
                        stale_sibling_hosts[host_key] = 'remove-stale'

        drift = _hosts_drift(live_hosts, expected)
        if stale_sibling_hosts:
            drift = {**drift, **stale_sibling_hosts}
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
