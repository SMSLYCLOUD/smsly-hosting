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
import os
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


def _is_worker_node_runtime() -> bool:
    """True when running inside a full worker node (which runs its own Sablier)."""
    return (
        str(os.environ.get("MODE_NODE", "") or "").strip().lower() == "true"
        or str(os.environ.get("NODE_TYPE", "") or "").strip().lower() == "node"
    )


def _node_access_log_has_recent_traffic(service, idle_minutes: int) -> bool:
    """Best-effort check of local Caddy/Traefik access logs on worker nodes."""
    slug = str(getattr(service, "slug", "") or getattr(service, "name", "") or "").strip().lower()
    pub = str(getattr(service, "public_domain", "") or "").strip().lower()
    needles = [n for n in (pub, f"{slug}." if slug else "") if n]
    if not needles:
        return False
    now_ts = timezone.now().timestamp()
    cutoff_ts = now_ts - (idle_minutes * 60)
    for log_path in ("/var/log/caddy/access.log", "/var/log/traefik/access.log"):
        try:
            st = os.stat(log_path)
            if st.st_mtime < cutoff_ts:
                continue
            with open(log_path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 256 * 1024))
                tail = fh.read().decode("utf-8", errors="ignore").lower()
            for needle in needles:
                if needle in tail:
                    return True
        except Exception:
            continue
    return False


def _service_is_idle(service, idle_minutes: int) -> bool:
    """No recorded traffic or recent Sablier wake activity within the window."""
    import time

    svc_id = getattr(service, "id", None)
    if svc_id:
        try:
            from django.core.cache import cache

            last_ts = cache.get(f"sablier:last_activity:{svc_id}")
            if isinstance(last_ts, (int, float)) and (time.time() - float(last_ts)) < (idle_minutes * 60):
                return False
        except Exception:
            pass
    if _is_worker_node_runtime() and _node_access_log_has_recent_traffic(service, idle_minutes):
        return False
    try:
        from apps.deployments.models.traffic import ServiceTrafficLog

        cutoff = timezone.now() - timedelta(minutes=idle_minutes)
        return not ServiceTrafficLog.objects.filter(
            service=service, last_seen__gte=cutoff,
        ).exists()
    except Exception:
        return False


def _container_recently_started(container, grace_minutes: int) -> bool:
    """True when Docker reports the container started within *grace_minutes*."""
    try:
        attrs = getattr(container, "attrs", None)
        if not isinstance(attrs, dict):
            return False
        state = attrs.get("State")
        if not isinstance(state, dict):
            return False
        raw_started = state.get("StartedAt")
        if not isinstance(raw_started, str) or len(raw_started) < 19:
            return False
        from datetime import datetime as _dt, timezone as _tz

        cleaned = raw_started.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        if "." in cleaned:
            head, tail = cleaned.split(".", 1)
            tz_idx = max(tail.find("+"), tail.find("-"))
            if tz_idx != -1:
                cleaned = f"{head}.{tail[:6]}{tail[tz_idx:]}"
            else:
                cleaned = f"{head}.{tail[:6]}+00:00"
        started_dt = _dt.fromisoformat(cleaned)
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=_tz.utc)
        age_seconds = (timezone.now() - started_dt).total_seconds()
        return 0 <= age_seconds < (grace_minutes * 60)
    except Exception:
        return False


def _find_service_containers(client, service, name: str) -> list:
    """Resolve primary container or Compose stack containers owned by *service*."""
    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator

    try:
        candidate = client.containers.get(name)
        if not DeletionOrchestrator._is_foreign_or_platform_resource(
            service, _container_labels(candidate), getattr(candidate, "name", name)
        ):
            return [candidate]
    except Exception:
        pass
    svc_id = str(getattr(service, "id", "") or "").strip()
    if not svc_id:
        return []
    try:
        matches = []
        for c in client.containers.list(filters={"label": f"smsly.service_id={svc_id}"}) or []:
            cname = str(getattr(c, "name", "") or "")
            if any(tag in cname for tag in ("-rollback-", "-green-", "-promoting-")):
                continue
            if cname.startswith("envoy-"):
                continue
            if DeletionOrchestrator._is_foreign_or_platform_resource(
                service, _container_labels(c), cname
            ):
                continue
            matches.append(c)
        return matches
    except Exception:
        return []


def _sleep_sablier_services(client) -> dict:
    slept: list[str] = []
    skipped: dict[str, str] = {}
    if str(os.environ.get("MODE_AGENT_LITE", "") or "").strip().lower() == "true":
        return {"slept": slept, "skipped": {"*": "agent-lite has no sablier"}}
    is_worker_node = _is_worker_node_runtime()
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
        svc_status = str(getattr(service, "status", "") or "").upper()
        if svc_status in ("STOPPED", "DELETED", "DELETION_PENDING"):
            skipped[name] = f"service {svc_status.lower()}"
            continue
        # Locality check:
        # - Lite agents never run Sablier and must always be skipped.
        # - Master Sablier skips remote services ("no master wake path").
        # - Full worker nodes run their own Sablier daemon and backstop-sleep
        #   containers running locally on that node.
        try:
            target = str(getattr(service, "active_target_type", "") or "").lower()
            server = getattr(service, "server", None)
            if server is None:
                try:
                    from apps.deployments.services.caddy_manager.config_generation import (
                        _resolve_effective_server,
                    )
                    eff = _resolve_effective_server(service)
                    if eff is not None and not getattr(eff, "is_primary", True):
                        server = eff
                except Exception:
                    pass
            if target == "lite_agent" or (server is not None and getattr(server, "is_lite_agent", False) is True):
                skipped[name] = "lite_agent: no sablier on lite agent"
                continue
            if not is_worker_node and (
                target == "remote"
                or (server is not None and not getattr(server, "is_primary", True))
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
        containers = _find_service_containers(client, service, name)
        if not containers:
            skipped[name] = "no local container"
            continue
        container = containers[0]
        extra_containers = containers[1:]
        try:
            if getattr(container, "status", "") != "running":
                skipped[name] = f"already {getattr(container, 'status', 'not-running')}"
                continue
            if _container_recently_started(container, session_min * 2):
                skipped[name] = "recently started/woken"
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
            # Stop any remaining Compose-group containers that belong to the same group.
            for extra_c in extra_containers:
                try:
                    if getattr(extra_c, "status", "") != "running":
                        continue
                    ex_ok, _ = _wake_path_enrolled(extra_c)
                    if ex_ok:
                        extra_c.stop(timeout=30)
                        logger.info(
                            "Sleeper: co-slept group container %s of %s",
                            getattr(extra_c, "name", "?"), name,
                        )
                except Exception:
                    pass
            # Co-sleep local RUNNING replicas that are Sablier-enrolled so
            # scaled-out replicas don't keep burning RAM when the service
            # goes idle (Sablier wakes the full group via sablier.group).
            try:
                from apps.autoscaler.models.replica import ServiceReplica

                for rep in ServiceReplica.objects.filter(
                    service=service,
                    node__isnull=True,
                    status="RUNNING",
                ):
                    cname = (getattr(rep, "container_name", "") or "").strip()
                    if not cname:
                        continue
                    try:
                        rc = client.containers.get(cname)
                        if getattr(rc, "status", "") != "running":
                            continue
                        rep_enrolled, rep_why = _wake_path_enrolled(rc)
                        if not rep_enrolled:
                            logger.warning(
                                "Sleeper: NOT sleeping replica %s of %s: %s",
                                cname, name, rep_why,
                            )
                            continue
                        rc.stop(timeout=30)
                        logger.info("Sleeper: co-slept replica %s of %s", cname, name)
                    except Exception:
                        pass
            except Exception as rep_exc:
                logger.debug("Sleeper: replica co-sleep skipped for %s: %s", name, rep_exc)
            # Co-sleep the Envoy sidecar (if any) so sablier's group
            # state stays consistent: wake starts the whole group
            # together, and the repair beat never sees a lone sidecar
            # it would otherwise remove as an orphan.
            try:
                sidecar = client.containers.get(f"envoy-{name}")
                from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
                sidecar_labels = _container_labels(sidecar)
                sidecar_svc = str(
                    sidecar_labels.get("com.paas.service")
                    or sidecar_labels.get("smsly.blue_green.canonical_name")
                    or ""
                ).strip()
                if (
                    getattr(sidecar, "status", "") == "running"
                    and not DeletionOrchestrator._is_foreign_or_platform_resource(
                        service, sidecar_labels, ""
                    )
                    and (not sidecar_svc or sidecar_svc == name)
                ):
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
