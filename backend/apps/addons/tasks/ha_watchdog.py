"""HA watchdog for addon replication topologies.

Runs every 60s via Celery beat and keeps ``Addon.ha_status`` truthful:

- REDIS (sentinel mode): verifies both data containers are running and
  at least two sentinels are up. Sentinel handles promotion itself; the
  watchdog only reports DEGRADED/FAILED_OVER state.
- POSTGRES (watchdog mode): if the primary stops serving writes for
  three consecutive checks, promotes the streaming standby and moves
  the friendly network alias onto it — automatic failover with no app
  reconfiguration.
"""
import logging
import os
import subprocess
import time

from celery import shared_task

from apps.addons.services.addon_ha import AddonHaManager
from apps.deployments.constants import (
    RETRY_DELAY_STANDARD,
    TASK_TIME_LIMIT_PROVISION,
    TASK_TIME_LIMIT_QUICK,
)
from apps.deployments.models.addons import Addon

logger = logging.getLogger(__name__)

# Consecutive failed probes before a Postgres failover is triggered.
# Beat runs this task every 30s, so default detection ≈ 1 minute.
try:
    _PG_FAILURES_TO_FAILOVER = max(
        1, int(os.environ.get('ADDON_HA_FAILOVER_PROBES', '2')))
except (TypeError, ValueError):
    _PG_FAILURES_TO_FAILOVER = 2

# Remote warm-DR mode: perform cutover automatically instead of waiting
# for a human to POST /promote-ha/. Services are redeployed afterwards so
# they pick up the rewritten DATABASE_URL.
_AUTO_CUTOVER = os.environ.get('ADDON_HA_REMOTE_AUTO_CUTOVER', 'true').strip().lower() \
    in ('1', 'true', 'yes', 'on')


def _find_dependent_services(addon):
    """Services whose env references this addon beyond its owner's service.

    Two linkage forms exist:
    - Owning service: ``addon.service`` (always first).
    - Ecosystem siblings: any service owned by the same user/project whose
      stored env vars contain ``{{REDIS_URL}}``-style placeholders resolving
      to this addon's type (see ecosystem/helpers/env_vars.py), or that
      embed the old connection URL literally.

    Values are encrypted at rest, so the scan happens in Python and is
    bounded: only same-owner/same-project services, capped at 200.
    """
    import re

    from apps.deployments.models import Service
    from apps.deployments.models.environment import EnvironmentVariable
    from apps.deployments.tasks.ecosystem.helpers.addons import (
        _addon_type_from_placeholder,
    )

    own = addon.service
    if own is None:
        return []

    found: dict[str, object] = {str(own.id): own}

    owner_id = getattr(own, 'owner_id', None)
    project_id = getattr(own, 'project_id', None)
    qs = Service.objects.all()
    if owner_id:
        qs = qs.filter(owner_id=owner_id)
    if project_id:
        qs = qs.filter(project_id=project_id)
    candidates = list(qs.exclude(id=own.id)[:200])

    if not candidates:
        return [found[str(own.id)]]

    token_re = re.compile(r"\{\{(.+?)\}\}")
    wanted_type = str(addon.addon_type or '').upper()
    old_url = str(getattr(addon, 'connection_url', '') or '')

    env_rows = EnvironmentVariable.objects.filter(
        service__in=[c.id for c in candidates]).select_related('service')
    for ev in env_rows:
        value_text = str(getattr(ev, 'value', '') or '')
        matched = False
        for m in token_re.finditer(value_text):
            try:
                if _addon_type_from_placeholder(m.group(1)) == wanted_type:
                    matched = True
                    break
            except Exception:
                continue
        if not matched and old_url and old_url in value_text:
            matched = True
        if matched:
            found[str(ev.service_id)] = ev.service

    return list(found.values())


def _queue_service_redeploys(addon) -> list[str]:
    from apps.deployments.models import Deployment
    from apps.deployments.models.audit import AuditLog
    from apps.deployments.models.core import Service
    from apps.deployments.tasks.deployment.tasks_deploy import (
        enqueue_smart_deploy_task,
    )

    affected: list[Service] = _find_dependent_services(addon)
    queued: list[str] = []
    for svc in affected:
        busy = Deployment.objects.filter(
            service=svc,
            status__in=[Deployment.Status.QUEUED, Deployment.Status.BUILDING],
        ).exists()
        if busy:
            logger.warning(
                "ha_watchdog(%s): service %s already has a deployment in "
                "flight; skipping auto-redeploy", addon.id, svc.name,
            )
            continue

        dep = Deployment.objects.create(
            service=svc,
            status=Deployment.Status.QUEUED,
            commit_message=f"Addon HA failover: {addon.name} endpoint updated",
        )
        enqueue_smart_deploy_task(
            str(dep.id),
            str(svc.provider.id) if getattr(svc, 'provider', None) else None,
        )
        AuditLog(
            actor='system:addon-ha',
            action='ADDON_HA_AUTO_REDEPLOY',
            target=f'Service: {svc.name}',
            metadata={
                'service_id': str(svc.id),
                'addon_id': str(addon.id),
                'deployment_id': str(dep.id),
            },
        ).save()
        queued.append(str(svc.id))

    if not queued and affected:
        logger.warning(
            "ha_watchdog(%s): %d dependent service(s) found but none "
            "queued", addon.id, len(affected),
        )
    return queued


def _container_running(name: str) -> bool:
    result = subprocess.run(
        ['docker', 'inspect', '-f', '{{.State.Running}}', name],
        capture_output=True, text=True, timeout=15,
    )
    return result.stdout.strip() == 'true'


@shared_task(
    bind=True,
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    max_retries=1,
    default_retry_delay=RETRY_DELAY_STANDARD,
    name="apps.addons.tasks.ha_watchdog.check_addon_ha_task",
)
def check_addon_ha_task(self):
    """Beat task: health-check every HA-enabled addon; fail over Postgres."""
    addons = Addon.objects.filter(ha_enabled=True).exclude(
        status=Addon.Status.DELETED)
    summary = {'checked': 0, 'healthy': 0, 'degraded': 0, 'failed_over': 0}
    for addon in addons:
        try:
            outcome = _check_one(addon)
            summary[outcome] = summary.get(outcome, 0) + 1
        except Exception:
            logger.exception("ha_watchdog: check failed for addon %s", addon.id)
    logger.debug("ha_watchdog: %s", summary)
    return summary


@shared_task(
    bind=True,
    soft_time_limit=TASK_TIME_LIMIT_PROVISION[0],
    time_limit=TASK_TIME_LIMIT_PROVISION[1],
    max_retries=2,
    default_retry_delay=RETRY_DELAY_STANDARD,
    name="apps.addons.tasks.ha_watchdog.reseed_postgres_standby_task",
)
def reseed_postgres_standby_task(self, addon_id: str):
    """Rebuild a fenced old primary as a streaming standby of the new master.

    Queued by the watchdog right after promotion. Large databases can take
    many minutes to seed via pg_basebackup, hence the 30-minute budget.
    """
    from django.core.cache import cache

    try:
        addon = Addon.objects.get(id=addon_id)
    except Addon.DoesNotExist:
        return {'skipped': 'addon deleted'}

    if not addon.ha_enabled or addon.addon_type != Addon.Type.POSTGRES:
        return {'skipped': 'ha not active'}

    manager = AddonHaManager(network_name='')
    master = manager._current_master_container(addon)
    if not master:
        raise RuntimeError('No master container answering; cannot reseed')

    name = manager.reseed_as_standby(addon, master)

    # Persist the role swap so future probes/promotions target the right
    # containers — topology['primary'] is now the promoted node and the
    # reseeded old primary becomes the standby.
    swapped = manager.swap_pg_roles(addon, master)
    type(addon).objects.filter(pk=addon.pk).update(
        ha_topology={**addon.ha_topology, **swapped})
    addon.ha_topology = {**addon.ha_topology, **swapped}

    _set_status(addon, Addon.HaStatus.HEALTHY)
    logger.info("reseed_postgres_standby(%s): standby %s streaming from %s",
                addon_id, name, master)
    cache.set(f"addon_ha_pg_failures:{addon_id}", 0, timeout=600)
    return {'standby': name, 'master': master}


def _check_one(addon) -> str:
    manager = AddonHaManager(network_name='')

    if addon.addon_type == Addon.Type.POSTGRES:
        return _check_postgres(addon, manager)

    if addon.addon_type == Addon.Type.REDIS:
        return _check_redis(addon, manager)

    return 'checked'


def _check_postgres(addon, manager: AddonHaManager) -> str:
    topology = addon.ha_topology or {}
    remote_placement = topology.get('placement') == 'remote'

    # After a completed cutover the roles have swapped; probe the CURRENT
    # master (topology['primary']) instead of the derived name.
    current_master, current_standby = manager.pg_role_containers(addon)
    failed_over_once = bool(topology.get('cutover_done'))

    if remote_placement and failed_over_once:
        return _check_remote_post_cutover(addon, manager, topology)

    alive = None
    try:
        alive = manager.is_postgres_primary_alive_container(
            current_master, addon)
    except Exception:
        logger.warning("ha_watchdog(%s): primary probe errored", addon.id)

    failures_key = f"addon_ha_pg_failures:{addon.id}"
    from django.core.cache import cache

    if alive is True:
        cache.set(failures_key, 0, timeout=600)
        # Steady state (incl. after a reseed cycle) is HEALTHY again.
        _set_status(addon, Addon.HaStatus.HEALTHY)
        return 'healthy'

    failures = (cache.get(failures_key) or 0) + 1
    cache.set(failures_key, failures, timeout=3600)
    logger.warning(
        "ha_watchdog(%s): postgres primary %s unhealthy (%d/%d)",
        addon.id, current_master, failures, _PG_FAILURES_TO_FAILOVER,
    )

    if remote_placement:
        # Warm-DR mode: the alias cannot span docker hosts, so cutover
        # rewrites the connection URL and dependent services are redeployed
        # to pick it up. Automated unless ADDON_HA_REMOTE_AUTO_CUTOVER=false.
        if failures >= _PG_FAILURES_TO_FAILOVER and _AUTO_CUTOVER:
            logger.warning(
                "ha_watchdog(%s): primary down — AUTO-CUTOVER to remote "
                "standby %s", addon.id, current_standby,
            )
            try:
                manager.promote_remote_standby(addon)
                cache.set(failures_key, 0, timeout=600)
                updated_topology = manager.mark_cutover_done(addon)
                addon.ha_topology = {**topology, **updated_topology}
                _set_status(addon, Addon.HaStatus.FAILED_OVER)
                redeployed = _queue_service_redeploys(addon)
                logger.info(
                    "ha_watchdog(%s): remote cutover complete; redeploys "
                    "queued for services %s", addon.id, redeployed,
                )
                return 'failed_over'
            except Exception:
                logger.exception(
                    "ha_watchdog(%s): AUTO-CUTOVER failed; manual "
                    "promote-ha required", addon.id,
                )
                _set_status(addon, Addon.HaStatus.DEGRADED)
                return 'degraded'
        if failures >= _PG_FAILURES_TO_FAILOVER:
            logger.error(
                "ha_watchdog(%s): primary down and standby is REMOTE "
                "(warm DR). Auto-cutover disabled — manual action required: "
                "POST /addons/%s/promote-ha/", addon.id, addon.id,
            )
            _set_status(addon, Addon.HaStatus.DEGRADED)
            return 'degraded'
        _set_status(addon, Addon.HaStatus.DEGRADED)
        return 'degraded'

    standby_ok = False
    try:
        standby_ok = _container_running(current_standby)
    except Exception:
        pass

    if not standby_ok:
        _set_status(addon, Addon.HaStatus.DEGRADED)
        return 'degraded'

    if failures >= _PG_FAILURES_TO_FAILOVER and alive is not True:
        logger.warning("ha_watchdog(%s): PROMOTING standby %s",
                       addon.id, current_standby)
        try:
            promoted = manager.promote_postgres_standby(addon)
            cache.set(failures_key, 0, timeout=600)
            _set_status(addon, Addon.HaStatus.FAILED_OVER)

            # Record the role swap IMMEDIATELY so subsequent probes target
            # the new master — otherwise the next cycle would see the old
            # container (now a standby) as 'unhealthy primary' and promote
            # it back, flapping forever.
            swapped = manager.swap_pg_roles(addon, promoted)
            addon.ha_topology = {**topology, **swapped}
            type(addon).objects.filter(pk=addon.pk).update(
                ha_topology=addon.ha_topology)

            # Fence the old primary immediately so a late restart cannot
            # resurrect it as a rogue second master (split-brain), then
            # reseed it as a streaming standby of the promoted node.
            manager.fence_primary_container(addon, old_name=current_standby)

            from apps.addons.tasks.ha_watchdog import reseed_postgres_standby_task
            try:
                reseed_postgres_standby_task.delay(str(addon.id))
            except Exception:
                logger.error(
                    "ha_watchdog(%s): could not queue standby reseed; "
                    "old primary remains fenced. Run disable/re-enable HA "
                    "to restore the replica.", addon.id, exc_info=True,
                )
            return 'failed_over'
        except Exception:
            logger.exception("ha_watchdog(%s): promotion FAILED", addon.id)
            _set_status(addon, Addon.HaStatus.FAILED)
            return 'degraded'

    _set_status(addon, Addon.HaStatus.DEGRADED)
    return 'degraded'


def _check_remote_post_cutover(addon, manager: AddonHaManager, topology) -> str:
    """Post-cutover steady state for remote warm-DR placement.

    The local node is gone/fenced; health is judged by the promoted REMOTE
    node only. Never probes the dead local primary — starting it manually
    cannot fool the status into HEALTHY.
    """
    from django.core.cache import cache

    failures_key = f"addon_ha_pg_failures:{addon.id}"
    healthy = manager.remote_standby_healthy(addon)
    if healthy:
        cache.set(failures_key, 0, timeout=600)
        _set_status(addon, Addon.HaStatus.HEALTHY)
        return 'healthy'

    failures = (cache.get(failures_key) or 0) + 1
    cache.set(failures_key, failures, timeout=3600)
    logger.warning(
        "ha_watchdog(%s): remote master unreachable (%d consecutive)",
        addon.id, failures,
    )
    _set_status(addon, Addon.HaStatus.FAILED)
    return 'degraded'


def _check_redis(addon, manager: AddonHaManager) -> str:
    topology = addon.ha_topology or {}
    replica = getattr(addon, 'replica_container_name', '') \
        or topology.get('replica') or ''
    primary = manager.primary_container(addon)

    data_up = []
    for name in filter(None, (primary, replica)):
        try:
            data_up.append(_container_running(name))
        except Exception:
            data_up.append(False)

    sentinels_up = 0
    for name in topology.get('sentinels', []):
        try:
            sentinels_up += 1 if _container_running(name) else 0
        except Exception:
            pass

    master = None
    try:
        master = manager._current_master_container(addon)
    except Exception:
        pass

    if all(data_up) and len(data_up) == 2 and sentinels_up >= 2 and master:
        _set_status(addon, Addon.HaStatus.HEALTHY)
        return 'healthy'
    if any(data_up) or sentinels_up >= 2 or master:
        _set_status(addon, Addon.HaStatus.DEGRADED)
        return 'degraded'
    _set_status(addon, Addon.HaStatus.FAILED)
    return 'degraded'


def _set_status(addon, new_status) -> None:
    if addon.ha_status != new_status:
        type(addon).objects.filter(pk=addon.pk).update(ha_status=new_status)
        addon.ha_status = new_status
