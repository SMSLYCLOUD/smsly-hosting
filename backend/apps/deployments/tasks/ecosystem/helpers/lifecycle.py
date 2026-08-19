import logging
import os
from collections import defaultdict

from django.core.cache import cache
from django.utils import timezone

from apps.deployments.models import Deployment
from apps.deployments.tasks.ecosystem.constants import (
    _ACTIVE_BUILDS_CACHE_KEY,
    _BUILD_DEFER_SECONDS,
    _DEFAULT_WAVE_SIZE,
    _MAX_CONCURRENT_BUILDS,
    _MIN_FREE_MEMORY_MB,
    _WAVE_RECHECK_SECONDS,
)

logger = logging.getLogger(__name__)


def _get_available_memory_mb() -> int:
    """Return available RAM in MB, or a large default if psutil is unavailable."""
    try:
        import psutil
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except ImportError:
        logger.warning("psutil unavailable — using conservative 512 MB memory estimate")
        return 512


def _has_enough_memory(min_free_mb: int = _MIN_FREE_MEMORY_MB) -> bool:
    """Check if system has at least min_free_mb of available memory."""
    free = _get_available_memory_mb()
    if free >= min_free_mb:
        return True
    logger.warning("Low memory: %d MB available, need %d MB. Deferring wave.", free, min_free_mb)
    return False


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    """Read bounded int from env."""
    try:
        parsed = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _count_active_ecosystem_builds() -> int:
    """Count ecosystem deployments currently being built (from DB — source of truth).

    The cache counter drifts because _decrement is never reliably called.
    Counting from DB eliminates drift entirely.
    """
    try:
        from apps.deployments.models import Deployment
        active_statuses = {
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        }
        return Deployment.objects.filter(
            commit_hash="ecosystem-deploy",
            status__in=active_statuses,
        ).count()
    except Exception:
        return 0


def _increment_active_ecosystem_builds() -> None:
    """Increment the active build counter (1-hour TTL safety net)."""
    try:
        try:
            cache.incr(_ACTIVE_BUILDS_CACHE_KEY)
        except (ValueError, ConnectionError):
            cache.add(_ACTIVE_BUILDS_CACHE_KEY, 1, timeout=3600)
    except Exception as exc:
        logger.debug("Failed to increment active build counter: %s", exc)


def _decrement_active_ecosystem_builds() -> None:
    """Decrement the active build counter."""
    try:
        current = int(cache.get(_ACTIVE_BUILDS_CACHE_KEY, 0))
        if current > 0:
            cache.set(_ACTIVE_BUILDS_CACHE_KEY, current - 1, timeout=3600)
    except Exception as exc:
        logger.debug("Failed to decrement active build counter: %s", exc)


def _rebuild_ecosystem_build_counter() -> None:
    """Recalculate the build counter from actual deployment statuses.
    Called periodically to prevent drift from stale cache entries."""
    try:
        active_statuses = {
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        }
        count = Deployment.objects.filter(
            commit_hash="ecosystem-deploy",
            status__in=active_statuses,
        ).count()
        cache.set(_ACTIVE_BUILDS_CACHE_KEY, count, timeout=3600)
    except Exception as exc:
        logger.debug("Failed to sync active build counter: %s", exc)


def _get_ecosystem_build_config() -> dict:
    """Read ecosystem build settings from PlatformConfig with env var fallback."""
    try:
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        max_concurrent = cfg.ecosystem_max_concurrent_builds or _MAX_CONCURRENT_BUILDS
        stagger = cfg.ecosystem_build_stagger_seconds or 30
        wave_size = cfg.ecosystem_default_wave_size or _DEFAULT_WAVE_SIZE
        recheck = cfg.ecosystem_wave_recheck_seconds or _WAVE_RECHECK_SECONDS
    except Exception:
        max_concurrent = _MAX_CONCURRENT_BUILDS
        stagger = 30
        wave_size = _DEFAULT_WAVE_SIZE
        recheck = _WAVE_RECHECK_SECONDS
    return {
        "max_concurrent_builds": max_concurrent,
        "build_stagger_seconds": stagger,
        "wave_size": wave_size,
        "wave_recheck_seconds": recheck,
    }


def _wave_recheck_countdown() -> int:
    """Return wave recheck countdown in seconds from PlatformConfig."""
    return _get_ecosystem_build_config()["wave_recheck_seconds"]


def _queue_wave(app, deployment_ids: list[str], provider_id: str, wave_index: int) -> int:
    """Queue QUEUED deployments in this wave with concurrency control."""

    queued = 0
    build_cfg = _get_ecosystem_build_config()
    max_concurrent = build_cfg["max_concurrent_builds"]
    stagger = build_cfg["build_stagger_seconds"]

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
                f"\n[Ecosystem] Build concurrency limit reached — deferred in wave {wave_index + 1}.\n"
            )
            deployment.save(update_fields=["build_logs"])
            app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_deferred_build_task",
                args=[str(deployment.id), str(provider_id), wave_index],
                countdown=_BUILD_DEFER_SECONDS,
            )
            continue

        _increment_active_ecosystem_builds()

        countdown = i * stagger
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Queued in wave {wave_index + 1} (stagger +{countdown}s).\n"
        )
        deployment.save(update_fields=["build_logs"])

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
            "STARTING",
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
