import logging
import os
import shutil
import time
from collections import defaultdict
from functools import lru_cache

from django.core.cache import cache as django_cache
from django.utils import timezone

from apps.deployments.models import Deployment
from apps.deployments.tasks.ecosystem.constants import (
    _ACTIVE_BUILDS_CACHE_KEY,
    _ACTIVE_BUILD_IDLE_MINUTES,
    _BUILD_DEFER_SECONDS,
    _DEFAULT_WAVE_SIZE,
    _MAX_CONCURRENT_BUILDS,
    _MAX_WAVE_SIZE,
    _MIN_FREE_MEMORY_MB,
    _WAVE_RECHECK_SECONDS,
)

logger = logging.getLogger(__name__)


_MB = 1024 * 1024
_CAPACITY_CACHE_KEY = "smsly:ecosystem:host_capacity"
_CAPACITY_CACHE_TTL_SECONDS = 30


def _get_system_capacity() -> dict:
    """Return a snapshot of the host's compute and memory capacity.

    Uses psutil when available; falls back to /proc/meminfo + os.cpu_count()
    so the logic still works in slim containers that don't ship psutil.
    Result is cached in Django's cache (or an in-process dict if the
    cache backend isn't ready) for 30s to avoid hammering /proc on
    every wave.
    """
    # Fast path: return from cache if recent
    try:
        cached = django_cache.get(_CAPACITY_CACHE_KEY)
        if isinstance(cached, dict) and cached.get("cached_at"):
            if (time.time() - cached["cached_at"]) < _CAPACITY_CACHE_TTL_SECONDS:
                return {k: v for k, v in cached.items() if k != "cached_at"}
    except Exception:
        pass  # cache backend not ready (e.g. during boot)

    cpu_count = os.cpu_count() or 1

    total_mb = 0
    available_mb = 0
    try:
        import psutil
        vm = psutil.virtual_memory()
        total_mb = int(vm.total / _MB)
        available_mb = int(vm.available / _MB)
    except ImportError:
        try:
            with open("/proc/meminfo") as fh:
                lines = fh.read().splitlines()
            kv = {}
            for line in lines:
                key, _, rest = line.partition(":")
                kv[key.strip()] = rest.strip()
            total_mb = int(kv.get("MemTotal", "0").split()[0]) // 1024
            available_mb = int(kv.get("MemAvailable", kv.get("MemFree", "0")).split()[0]) // 1024
        except Exception:
            total_mb = 0
            available_mb = 0
    except Exception as exc:
        logger.debug("psutil memory read failed: %s", exc)

    disk_free_gb = 0
    try:
        disk_free_gb = int(shutil.disk_usage("/").free / (1024 ** 3))
    except Exception as exc:
        logger.debug("disk usage read failed: %s", exc)

    result = {
        "cpu_count": cpu_count,
        "total_memory_mb": total_mb,
        "available_memory_mb": available_mb,
        "disk_free_gb": disk_free_gb,
        "cached_at": time.time(),
    }

    # Best-effort write to Django cache (silently ignore failures)
    try:
        django_cache.set(_CAPACITY_CACHE_KEY, result, timeout=_CAPACITY_CACHE_TTL_SECONDS + 5)
    except Exception:
        pass

    return {k: v for k, v in result.items() if k != "cached_at"}


def _get_available_memory_mb() -> int:
    """Return available RAM in MB, or a large default if it can't be measured."""
    cap = _get_system_capacity()
    if cap["available_memory_mb"] > 0:
        return cap["available_memory_mb"]
    logger.warning("Memory measurement unavailable — using conservative 512 MB estimate")
    return 512


# ── Per-build memory budget ───────────────────────────────────────────────
# A build runs `nixpacks` or `docker build`, then `docker compose up`. Worst
# case memory: ~1.5 GB per build (Nixpacks + compose resolution + layer cache
# for a mid-size app). On a 8 GB host with 1 GB reserved for infra, this gives
# 4 parallel builds at 1.5 GB each. Tuneable via PlatformConfig.
_DEFAULT_BUILD_MEMORY_MB = 1500
_SAFETY_RESERVE_MB = 1024        # Always keep this much free for infra/celery
_MIN_CONCURRENCY = 1
_MAX_CONCURRENCY = 12            # Hard ceiling regardless of how much RAM we have
_DYNAMIC_WAVE_MIN = 1
_DYNAMIC_WAVE_MAX_CAP = 10       # Don't ever build a wave bigger than this


def _calculate_dynamic_concurrency(
    per_build_mb: int = _DEFAULT_BUILD_MEMORY_MB,
    safety_reserve_mb: int = _SAFETY_RESERVE_MB,
) -> int:
    """Compute the maximum number of concurrent builds based on available RAM.

    Returns a value between _MIN_CONCURRENCY and _MAX_CONCURRENCY inclusive.
    """
    cap = _get_system_capacity()
    free = cap["available_memory_mb"]
    if free <= 0 or per_build_mb <= 0:
        return _MIN_CONCURRENCY
    usable = max(0, free - safety_reserve_mb)
    by_memory = max(_MIN_CONCURRENCY, usable // per_build_mb)
    # CPU ceiling: leave at least 1 core for the host (celery, traefik, caddy)
    by_cpu = max(_MIN_CONCURRENCY, cap["cpu_count"] - 1)
    chosen = min(by_memory, by_cpu, _MAX_CONCURRENCY)
    logger.info(
        "Dynamic concurrency: %d MB free, %d cores, per_build=%d MB → max_concurrent=%d",
        free, cap["cpu_count"], per_build_mb, chosen,
    )
    return chosen


def _calculate_dynamic_wave_size(
    concurrency: int,
    per_build_mb: int = _DEFAULT_BUILD_MEMORY_MB,
    safety_reserve_mb: int = _SAFETY_RESERVE_MB,
) -> int:
    """Compute a wave size that fits comfortably in current memory budget.

    A wave launches at most `concurrency` builds in parallel, each consuming
    ~per_build_mb. We size the wave so that even if every slot starts at once
    we stay above the safety reserve.
    """
    cap = _get_system_capacity()
    free = cap["available_memory_mb"]
    if free <= 0:
        return min(_DYNAMIC_WAVE_MIN, _DYNAMIC_WAVE_MAX_CAP)
    usable = max(0, free - safety_reserve_mb)
    if per_build_mb <= 0:
        return _DYNAMIC_WAVE_MIN
    by_memory = max(_DYNAMIC_WAVE_MIN, usable // per_build_mb)
    chosen = min(
        by_memory,
        max(concurrency, _DYNAMIC_WAVE_MIN),
        _DYNAMIC_WAVE_MAX_CAP,
    )
    return chosen


def _has_enough_memory(required_mb: int = _MIN_FREE_MEMORY_MB) -> bool:
    """Check if the system has at least ``required_mb`` free RAM available.

    The required_mb is *added* to the safety reserve, so callers asking for
    2048 MB effectively need 2 GB free above the 1 GB reserve.
    """
    cap = _get_system_capacity()
    free = cap["available_memory_mb"]
    if free <= 0:
        return True  # Can't measure — don't block
    needed = required_mb + _SAFETY_RESERVE_MB
    if free >= needed:
        return True
    logger.warning(
        "Low memory: %d MB free, need %d MB (build=%d + reserve=%d). Deferring wave.",
        free, needed, required_mb, _SAFETY_RESERVE_MB,
    )
    return False


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    """Read bounded int from env."""
    try:
        parsed = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _count_active_ecosystem_builds() -> int:
    """Count ecosystem builds actually consuming resources (from DB — source of truth).

    The cache counter drifts because _decrement is never reliably called.
    Counting from DB eliminates drift entirely.

    Only rows with a worker behind them count: QUEUED rows are *waiting
    for* a slot, so counting them deadlocks the gate (10 waiting rows
    read as "10 active builds" and nothing ever dispatches). REVIEW rows
    (dispatched, awaiting pickup) count only while fresh — a REVIEW row
    idle past the activity window is a dead dispatch, not a build.
    Ghost rows are reaped separately by recover_stalled_deployments.
    """
    try:
        from datetime import timedelta

        from apps.deployments.models import Deployment
        active_statuses = {
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        }
        fresh_cutoff = timezone.now() - timedelta(minutes=_ACTIVE_BUILD_IDLE_MINUTES)
        return Deployment.objects.filter(
            commit_hash="ecosystem-deploy",
            status__in=active_statuses,
            updated_at__gte=fresh_cutoff,
        ).count()
    except Exception:
        return 0


def _increment_active_ecosystem_builds() -> None:
    """Increment the active build counter (1-hour TTL safety net)."""
    try:
        try:
            django_cache.incr(_ACTIVE_BUILDS_CACHE_KEY)
        except (ValueError, ConnectionError):
            django_cache.add(_ACTIVE_BUILDS_CACHE_KEY, 1, timeout=3600)
    except Exception as exc:
        logger.debug("Failed to increment active build counter: %s", exc)


def _decrement_active_ecosystem_builds() -> None:
    """Decrement the active build counter."""
    try:
        current = int(django_cache.get(_ACTIVE_BUILDS_CACHE_KEY, 0))
        if current > 0:
            django_cache.set(_ACTIVE_BUILDS_CACHE_KEY, current - 1, timeout=3600)
    except Exception as exc:
        logger.debug("Failed to decrement active build counter: %s", exc)


def _rebuild_ecosystem_build_counter() -> None:
    """Recalculate the build counter from actual deployment statuses.
    Called periodically to prevent drift from stale cache entries.
    Mirrors _count_active_ecosystem_builds: waiting (QUEUED) and stale
    rows are not active builds."""
    try:
        from datetime import timedelta

        active_statuses = {
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        }
        fresh_cutoff = timezone.now() - timedelta(minutes=_ACTIVE_BUILD_IDLE_MINUTES)
        count = Deployment.objects.filter(
            commit_hash="ecosystem-deploy",
            status__in=active_statuses,
            updated_at__gte=fresh_cutoff,
        ).count()
        django_cache.set(_ACTIVE_BUILDS_CACHE_KEY, count, timeout=3600)
    except Exception as exc:
        logger.debug("Failed to sync active build counter: %s", exc)


def _get_ecosystem_build_config() -> dict:
    """Read ecosystem build settings with dynamic overrides.

    Order of precedence for ``max_concurrent_builds`` and ``wave_size``:
    1. PlatformConfig field (operator's static ceiling)
    2. Dynamic calculation from available RAM + CPU
    3. Hardcoded constant fallback

    This means the operator can cap concurrency with a single number on
    PlatformConfig, but if memory is tight we drop below that cap
    automatically. Likewise, if the operator leaves it at 0, we size
    based on real resources.
    """
    dynamic_concurrency = _calculate_dynamic_concurrency()
    try:
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        pc_concurrency = cfg.ecosystem_max_concurrent_builds or 0
        # Cap by the operator's static ceiling if set; otherwise use dynamic
        if pc_concurrency > 0:
            max_concurrent = min(dynamic_concurrency, pc_concurrency)
        else:
            max_concurrent = dynamic_concurrency

        # Stagger stays static — it's a politeness delay, not a resource gate
        stagger = cfg.ecosystem_build_stagger_seconds or 30

        # Wave size: dynamic baseline, then clipped by operator override
        dynamic_wave = _calculate_dynamic_wave_size(max_concurrent)
        pc_wave = cfg.ecosystem_default_wave_size or 0
        if pc_wave > 0:
            wave_size = min(dynamic_wave, pc_wave)
        else:
            wave_size = dynamic_wave
        wave_size = max(1, min(_MAX_WAVE_SIZE, wave_size))

        recheck = cfg.ecosystem_wave_recheck_seconds or _WAVE_RECHECK_SECONDS
    except Exception:
        max_concurrent = dynamic_concurrency
        stagger = 30
        wave_size = max(1, min(_MAX_WAVE_SIZE, _calculate_dynamic_wave_size(max_concurrent)))
        recheck = _WAVE_RECHECK_SECONDS

    cap = _get_system_capacity()
    logger.info(
        "Ecosystem build config: %d MB free, %d cores → max_concurrent=%d, wave_size=%d",
        cap["available_memory_mb"], cap["cpu_count"], max_concurrent, wave_size,
    )
    return {
        "max_concurrent_builds": max_concurrent,
        "build_stagger_seconds": stagger,
        "wave_size": wave_size,
        "wave_recheck_seconds": recheck,
    }


def _wave_recheck_countdown() -> int:
    """Return wave recheck countdown in seconds from PlatformConfig."""
    return _get_ecosystem_build_config()["wave_recheck_seconds"]


def _queue_wave(app, deployment_ids: list[str], provider_id: str, wave_index: int, plan_id: str | None = None) -> int:
    """Queue QUEUED deployments in this wave with dynamic concurrency control.

    Before queuing, the function re-checks available memory. If the wave
    would push us below the safety reserve, the entire wave is deferred
    (not partially queued) so we don't end up with a half-running set
    that OOMs the host.

    ``plan_id`` is threaded through to deferred-build tasks so they can
    stop re-scheduling themselves once their owning plan is finished.
    """

    queued = 0
    build_cfg = _get_ecosystem_build_config()
    max_concurrent = build_cfg["max_concurrent_builds"]
    stagger = build_cfg["build_stagger_seconds"]

    # Re-evaluate memory under the wave's own footprint. Each build
    # needs ~1.5 GB; the wave will at most launch `max_concurrent` at once.
    wave_memory_required = max_concurrent * _DEFAULT_BUILD_MEMORY_MB
    if not _has_enough_memory(wave_memory_required):
        for deployment_id in deployment_ids:
            deployment = Deployment.objects.filter(id=deployment_id).first()
            if not deployment or deployment.status != Deployment.Status.QUEUED:
                continue
            deployment.build_logs = (
                f"{deployment.build_logs or ''}"
                f"\n[Ecosystem] Wave {wave_index + 1} deferred — "
                f"insufficient free memory for {max_concurrent} concurrent builds.\n"
            )
            deployment.save(update_fields=["build_logs"])
            app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_deferred_build_task",
                args=[str(deployment.id), str(provider_id), wave_index, plan_id],
                countdown=_BUILD_DEFER_SECONDS,
            )
        return 0

    for i, deployment_id in enumerate(deployment_ids):
        deployment = Deployment.objects.filter(id=deployment_id).first()
        if not deployment:
            continue
        if deployment.status != Deployment.Status.QUEUED:
            continue

        # Check concurrency limit
        active = _count_active_ecosystem_builds()
        if active >= max_concurrent:
            deployment.build_logs = (
                f"{deployment.build_logs or ''}"
                f"\n[Ecosystem] Build concurrency limit reached "
                f"({active}/{max_concurrent}) — deferred in wave {wave_index + 1}.\n"
            )
            deployment.save(update_fields=["build_logs"])
            app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_deferred_build_task",
                args=[str(deployment.id), str(provider_id), wave_index, plan_id],
                countdown=_BUILD_DEFER_SECONDS,
            )
            continue

        _increment_active_ecosystem_builds()

        countdown = i * stagger
        # IDEMPOTENCY: flip to REVIEW ("dispatched, awaiting smart_deploy_task
        # pickup") at dispatch time. A re-invoked _queue_wave (wave task
        # re-send, worker redelivery) skips REVIEW rows — without this
        # marker, a dispatched-but-not-yet-started deployment stayed QUEUED
        # and every re-send double-dispatched smart_deploy_task.
        deployment.status = Deployment.Status.REVIEW
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Queued in wave {wave_index + 1} (stagger +{countdown}s).\n"
        )
        deployment.save(update_fields=["build_logs", "status"])

        app.send_task(
            "apps.deployments.tasks.smart_deploy_task",
            args=[str(deployment.id), str(provider_id)],
            kwargs={"skip_review": True},
            countdown=countdown,
        )
        queued += 1

    return queued


def _cancel_dependent_deployments(
    waves: list[list[str]],
    from_wave_index: int,
    failed_deployment_ids: list[str],
    dependencies: dict[str, set[str]],
    deployment_by_repo_key: dict[str, str],
    reason: str,
) -> int:
    """Cancel queued deployments in unreleased waves that depend on failed deployments."""

    # Reverse the mapping to find the repo_key from deployment_id
    repo_key_by_deployment = {v: k for k, v in deployment_by_repo_key.items()}

    # Identify which repo_keys failed
    failed_keys = {
        repo_key_by_deployment[dep_id]
        for dep_id in failed_deployment_ids
        if dep_id in repo_key_by_deployment
    }

    if not failed_keys:
        return 0

    # Build dependents map: parent -> set of children
    dependents: dict[str, set[str]] = defaultdict(set)
    for key, deps in dependencies.items():
        for dep in deps:
            dependents[dep].add(key)

    # Transitively find all nodes that depend on a failed node
    to_cancel_keys: set[str] = set()
    stack = list(failed_keys)
    while stack:
        node = stack.pop()
        for child in dependents.get(node, set()):
            if child not in to_cancel_keys and child not in failed_keys:
                to_cancel_keys.add(child)
                stack.append(child)

    if not to_cancel_keys:
        return 0

    to_cancel_ids = [
        deployment_by_repo_key[key]
        for key in to_cancel_keys
        if key in deployment_by_repo_key
    ]

    cancelled = 0
    to_update = []
    now = timezone.now()
    for deployment in Deployment.objects.filter(id__in=to_cancel_ids):
        if deployment.status != Deployment.Status.QUEUED:
            continue
        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = now
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Cancelled before execution: {reason}\n"
        )
        to_update.append(deployment)
        cancelled += 1
    if to_update:
        Deployment.objects.bulk_update(to_update, ["status", "finished_at", "build_logs"])

    return cancelled


def _cancel_all_remaining_deployments(
    waves: list[list[str]],
    from_wave_index: int,
    failed_deployment_ids: list[str],
    deployment_by_repo_key: dict[str, str],
    reason: str,
) -> int:
    """Cancel ALL queued deployments from *from_wave_index* onwards.

    Unlike ``_cancel_dependent_deployments`` which only cancels nodes
    that transitively depend on the failed node, this function cancels
    every remaining queued deployment in the ecosystem.  Used when
    ``cancel_others_on_failure`` is enabled.
    """
    failed_set = set(failed_deployment_ids)

    to_cancel_ids: list[str] = []
    for wave in waves[from_wave_index:]:
        for dep_id in wave:
            if dep_id in failed_set:
                continue
            to_cancel_ids.append(dep_id)

    if not to_cancel_ids:
        return 0

    cancelled = 0
    to_update = []
    now = timezone.now()
    for deployment in Deployment.objects.filter(id__in=to_cancel_ids):
        if deployment.status != Deployment.Status.QUEUED:
            continue
        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = now
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Cancelled (fail-fast mode): {reason}\n"
        )
        to_update.append(deployment)
        cancelled += 1
    if to_update:
        Deployment.objects.bulk_update(to_update, ["status", "finished_at", "build_logs"])

    return cancelled


def _cancel_unreleased_deployments(waves: list[list[str]], from_wave_index: int, reason: str) -> int:
    """Cancel remaining queued deployments when wave orchestration aborts (e.g. timeout)."""
    to_cancel_ids: list[str] = []
    for wave in waves[from_wave_index:]:
        to_cancel_ids.extend(wave)
    if not to_cancel_ids:
        return 0
    cancelled = 0
    to_update = []
    now = timezone.now()
    for deployment in Deployment.objects.filter(id__in=to_cancel_ids):
        if deployment.status != Deployment.Status.QUEUED:
            continue
        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = now
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Cancelled: {reason}\n"
        )
        to_update.append(deployment)
        cancelled += 1
    if to_update:
        Deployment.objects.bulk_update(to_update, ["status", "finished_at", "build_logs"])
    return cancelled


def _finalize_ecosystem_plan(plan_id: str | None, waves: list[list[str]]):
    """Sync final EcosystemPlan status when all waves have completed or aborted."""
    if not plan_id:
        return
    from apps.deployments.models.ecosystem import EcosystemPlan
    try:
        plan_rec = EcosystemPlan.objects.filter(id=plan_id).first()
        if not plan_rec:
            return
        if not waves:
            plan_rec.status = EcosystemPlan.Status.COMPLETED
            plan_rec.completed_at = timezone.now()
            plan_rec.error_message = ""
            plan_rec.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
            return
        all_ids = [str(dep_id) for wave in waves for dep_id in wave]
        deployments = list(Deployment.objects.filter(id__in=all_ids).values("status"))
        statuses = [d["status"] for d in deployments]
        failed_states = {
            Deployment.Status.FAILED,
            Deployment.Status.BUILD_FAILED,
            Deployment.Status.BACKUP_FAILED,
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.CANCELLED,
            # A deployment that failed its health check is a FAILURE for
            # the plan — previously it fell through both sets and the
            # plan could be marked COMPLETED with a dead service.
            Deployment.Status.HEALTH_CHECK_FAILED,
            Deployment.Status.ROLLED_BACK,
        }
        in_progress_states = {
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.AWAITING_APPROVAL,
            Deployment.Status.BACKUP_RUNNING,
            Deployment.Status.MIGRATION_PLANNING,
            Deployment.Status.MIGRATION_RUNNING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        }
        if any(st in in_progress_states for st in statuses):
            return  # Still running
        failed_count = sum(1 for st in statuses if st in failed_states)
        if failed_count == 0 and statuses:
            plan_rec.status = EcosystemPlan.Status.COMPLETED
            plan_rec.completed_at = timezone.now()
            plan_rec.error_message = ""
        else:
            plan_rec.status = EcosystemPlan.Status.FAILED
            plan_rec.error_message = f"Ecosystem deploy finished with {failed_count}/{len(statuses)} service failures or cancellations."
        _rebuild_ecosystem_build_counter()
        plan_rec.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
    except Exception as exc:
        logger.warning("Failed to finalize ecosystem plan %s: %s", plan_id, exc)

