"""Sleeper agent — puts idle things to sleep so RAM/CPU stop burning.

Two jobs, both backstops (the primary mechanisms own the fast path):

1. Sablier-enabled services (master-local only): Sablier's Traefik
   middleware wakes stopped containers on request and stops them when
   the session expires — but a missed expiry leaves the container
   running forever. When a sablier-enabled service shows NO traffic
   for 2x its session duration and is NOT deploying, the agent stops
   (never removes) the container. Wake stays automatic on next
   request. Enrollment is verified on the live container first
   (sablier.enable + sablier.group labels): rows deployed before
   enrollment carry no wake path, and sleeping those 503s with no
   wake attempt — so they are skipped until a redeploy enrolls them.
   Remote/node services are skipped — master Sablier cannot
   start node containers, and stopping them would strand traffic.
2. Orphaned buildkit builder containers (``buildx_buildkit_*`` with no
   builder referencing them): with the ``docker`` driver, builds run
   through the daemon — these orphans only hold RAM. Removed when no
   build ran platform-wide in the last 30 minutes.

Fail-open throughout: returns a report dict, never raises. Obs and
platform infra are NEVER touched (monitoring must stay up).
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_STANDARD

logger = logging.getLogger(__name__)

_BUILDKIT_ORPHAN_PREFIX = "buildx_buildkit_"
_NO_BUILD_QUIET_MINUTES = 30


def _parse_session_minutes(raw: object, default: int = 10) -> int:
    try:
        text = str(raw or "").strip().lower() or f"{default}m"
        if text.endswith("ms"):
            return max(1, int(float(text[:-2]) / 60000) or 1)
        if text.endswith("h"):
            return max(1, int(float(text[:-1]) * 60))
        if text.endswith("m"):
            return max(1, int(float(text[:-1])))
        if text.endswith("s"):
            return max(1, int(float(text[:-1]) / 60) or 1)
        return max(1, int(float(text)))
    except (TypeError, ValueError):
        return default


def _recent_build_activity(minutes: int = _NO_BUILD_QUIET_MINUTES) -> bool:
    """True when any deployment looked alive recently (do not sleep)."""
    try:
        from apps.deployments.models import Deployment

        cutoff = timezone.now() - timedelta(minutes=minutes)
        busy_statuses = ("QUEUED", "BUILDING", "DEPLOYING", "REVIEW", "HEALTH_CHECK")
        return Deployment.objects.filter(
            status__in=busy_statuses, updated_at__gte=cutoff,
        ).exists()
    except Exception:
        return True


def _container_labels(container) -> dict:
    """Create-time labels of a container (never raises; {} on failure)."""
    try:
        attrs = getattr(container, "attrs", None) or {}
        config = attrs.get("Config", None) or {}
        labels = config.get("Labels", None) or {}
        if isinstance(labels, dict) and labels:
            return labels
    except Exception:
        pass
    try:
        labels = getattr(container, "labels", None) or {}
        return dict(labels) if isinstance(labels, dict) else {}
    except Exception:
        return {}


def _wake_path_enrolled(container) -> tuple[bool, str]:
    """True when stopping this container is wakeable via sablier.

    Enrollment is create-time labels: ``sablier.enable=true`` plus a
    non-empty ``sablier.group`` (the middleware group key). A container
    deployed before enrollment (or via a path that skipped labels)
    must NEVER be slept — Traefik would 503 with no wake attempt.
    Unreadable labels fail closed (not enrolled).
    """
    try:
        labels = _container_labels(container)
        if str(labels.get("sablier.enable", "")).strip().lower() != "true":
            return False, "sablier.enable label missing"
        if not str(labels.get("sablier.group", "") or "").strip():
            return False, "sablier.group label missing"
        return True, ""
    except Exception as exc:
        return False, f"label read failed: {exc}"


def _service_is_idle(service, idle_minutes: int) -> bool:
    """No recorded traffic for this service within the window."""
    try:
        from apps.deployments.models.traffic import ServiceTrafficLog

        cutoff = timezone.now() - timedelta(minutes=idle_minutes)
        return not ServiceTrafficLog.objects.filter(
            service=service, last_seen__gte=cutoff,
        ).exists()
    except Exception:
        return False


def _sleep_sablier_services(client) -> dict:
    slept: list[str] = []
    skipped: dict[str, str] = {}
    try:
        from apps.deployments.models import Deployment, Service
    except Exception as exc:
        return {"slept": slept, "skipped": skipped, "error": f"models unavailable: {exc}"}
    try:
        candidates = list(Service.objects.filter(sablier_enabled=True))
    except Exception as exc:
        return {"slept": slept, "skipped": skipped, "error": f"query failed: {exc}"}
    for service in candidates:
        name = getattr(service, "name", "") or ""
        if not name:
            continue
        # Remote services: master Sablier cannot wake node containers.
        try:
            target = str(getattr(service, "active_target_type", "") or "").lower()
            server = getattr(service, "server", None)
            if target in ("remote", "lite_agent") or (
                server is not None and not getattr(server, "is_primary", True)
            ):
                skipped[name] = "remote: no master wake path"
                continue
        except Exception:
            skipped[name] = "locality check failed (fail-closed)"
            continue
        # Never sleep mid-deploy.
        try:
            busy = Deployment.objects.filter(
                service=service,
                status__in=("QUEUED", "BUILDING", "DEPLOYING", "REVIEW", "HEALTH_CHECK"),
                updated_at__gte=timezone.now() - timedelta(minutes=30),
            ).exists()
            if busy:
                skipped[name] = "deployment in progress"
                continue
        except Exception:
            skipped[name] = "deploy check failed (fail-closed)"
            continue
        session_min = _parse_session_minutes(getattr(service, "sablier_session", "10m"))
        if not _service_is_idle(service, session_min * 2):
            skipped[name] = "recent traffic"
            continue
        # Mesh-served apps must never sleep: sidecar-to-sidecar dials
        # bypass Traefik (no in-band wake path) and the provider Envoy
        # requires client certs, so a slept provider strands mesh
        # callers with no recovery. Label enrollment is likewise
        # withheld at deploy (see internal_wake); this covers rows
        # deployed before the exclusion. Availability over savings.
        try:
            from apps.deployments.services.internal_wake import (
                service_has_mesh_sidecar,
            )
            if service_has_mesh_sidecar(service):
                skipped[name] = "mesh-served: no internal wake path"
                continue
        except Exception:
            pass
        try:
            container = client.containers.get(name)
        except Exception:
            skipped[name] = "no local container"
            continue
        try:
            if getattr(container, "status", "") != "running":
                skipped[name] = f"already {getattr(container, 'status', 'not-running')}"
                continue
            # Fail-closed: never sleep a container with no wake path.
            # DB enrollment (sablier_enabled) is not enough — the wake
            # chain lives in create-time container labels, and rows
            # deployed before enrollment carry none (2026-10-17: all 9
            # enrolled services unlabeled live → every sleep ended in
            # 503 with no wake attempt). Redeploy enrolls; until then
            # the service stays up.
            enrolled, why = _wake_path_enrolled(container)
            if not enrolled:
                skipped[name] = f"no wake path enrolled ({why}; redeploy to enroll)"
                logger.warning("Sleeper: NOT sleeping %s: %s", name, why)
                continue
            container.stop(timeout=30)
            slept.append(name)
            logger.info("Sleeper: stopped idle sablier service %s", name)
            # Co-sleep the Envoy sidecar (if any) so sablier's group
            # state stays consistent: wake starts the whole group
            # together, and the repair beat never sees a lone sidecar
            # it would otherwise remove as an orphan.
            try:
                sidecar = client.containers.get(f"envoy-{name}")
                if getattr(sidecar, "status", "") == "running":
                    sidecar.stop(timeout=15)
                    logger.info("Sleeper: co-slept sidecar envoy-%s", name)
            except Exception:
                pass
        except Exception as exc:
            skipped[name] = f"stop failed: {exc}"
    return {"slept": slept, "skipped": skipped}


def _reap_orphan_builders(client) -> dict:
    """Remove orphaned buildkit builder containers (docker driver builds
    don't use them; a referenced builder is never touched)."""
    removed: list[str] = []
    try:
        containers = client.containers.list(all=True, filters={"name": _BUILDKIT_ORPHAN_PREFIX})
    except Exception as exc:
        return {"removed": removed, "error": f"list failed: {exc}"}
    orphans = [
        c for c in containers
        if (getattr(c, "name", "") or "").startswith(_BUILDKIT_ORPHAN_PREFIX)
    ]
    if not orphans:
        return {"removed": removed}
    if _recent_build_activity():
        return {"removed": removed, "skipped": f"{len(orphans)} orphan(s) kept: recent build activity"}
    for container in orphans:
        try:
            container.remove(force=True)
            removed.append(container.name)
            logger.info("Sleeper: removed orphan buildkit container %s", container.name)
        except Exception as exc:
            logger.debug("Sleeper: orphan remove failed for %s: %s", getattr(container, "name", "?"), exc)
    return {"removed": removed}


@shared_task(
    soft_time_limit=TASK_TIME_LIMIT_STANDARD[0],
    time_limit=TASK_TIME_LIMIT_STANDARD[1],
    name="apps.autoscaler.tasks.sleeper_agent_task",
)
def sleeper_agent_task() -> dict:
    """Stop idle sablier-enabled services + reap orphan builders."""
    report: dict = {"sablier": {}, "buildkit": {}}
    try:
        import docker as _docker

        client = _docker.from_env(timeout=10)
    except Exception as exc:
        logger.debug("Sleeper: docker unavailable: %s", exc)
        return {"status": "skipped", "reason": f"docker unavailable: {exc}", **report}
    try:
        report["sablier"] = _sleep_sablier_services(client)
    except Exception as exc:
        report["sablier"] = {"error": str(exc)[:200]}
    try:
        report["buildkit"] = _reap_orphan_builders(client)
    except Exception as exc:
        report["buildkit"] = {"error": str(exc)[:200]}
    report["status"] = "ok"
    logger.info(
        "Sleeper: slept=%s removed_builders=%s",
        report["sablier"].get("slept"), report["buildkit"].get("removed"),
    )
    return report
