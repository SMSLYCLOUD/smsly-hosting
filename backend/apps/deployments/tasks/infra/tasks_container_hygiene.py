"""Container hygiene beat tasks.

* restart-loop watchdog — a container crash-looping (like the redis addon
  that hit 1,875 restarts unnoticed) pages within minutes instead of hours.
* orphan addon GC — codifies the alias-aware sweep: any smsly-addon-*
  container without a live DB record, or shadowing a canonical alias, is
  removed. Ran manually twice during the 2026-08 incident; now automatic.
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
