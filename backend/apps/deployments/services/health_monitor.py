# pylint: disable=invalid-name
"""
Health monitoring with guarded auto-restart.

Design goals:
- Respect each service's health_check_interval.
- Keep failure/restart state shared across workers (cache-backed).
- Prevent restart storms with backoff + max restart cap.
- Avoid duplicate restarts while another deployment is already in progress.
- Detect crashed containers via Docker state for instant fail-fast.
"""
import logging
import os
import time

import requests
from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.services.tls_verify import should_verify
from apps.deployments.utils import log_event

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


RESTART_COOLDOWN_BASE = _env_int(
    "HEALTH_RESTART_COOLDOWN_BASE_SECONDS",
    600,
    minimum=1,
)
MAX_AUTO_RESTARTS = _env_int("HEALTH_MAX_AUTO_RESTARTS", 3, minimum=1)
BACKOFF_MULTIPLIER = _env_float("HEALTH_RESTART_BACKOFF_MULTIPLIER", 2.0, minimum=1.0)
STARTUP_GRACE_SECONDS = _env_int("HEALTH_STARTUP_GRACE_SECONDS", 300, minimum=0)
LOW_RESOURCE_EXTRA_GRACE_SECONDS = _env_int(
    "HEALTH_LOW_RESOURCE_EXTRA_GRACE_SECONDS",
    120,
    minimum=0,
)
# When a container is dead (exited/crashed), use a fast
# retry threshold instead of the full HTTP retry count.
CRASH_FAST_RETRIES = _env_int("HEALTH_CRASH_FAST_RETRIES", 3, minimum=1)
LOW_RESOURCE_CPU_THRESHOLD = _env_float(
    "HEALTH_LOW_RESOURCE_CPU_THRESHOLD",
    0.75,
    minimum=0.1,
)
LOW_RESOURCE_MEMORY_THRESHOLD_MB = _env_int(
    "HEALTH_LOW_RESOURCE_MEMORY_THRESHOLD_MB",
    768,
    minimum=64,
)

# Keep state long enough to survive process restarts and long cooldown windows.
_MAX_COOLDOWN_SECONDS = int(
    RESTART_COOLDOWN_BASE * (BACKOFF_MULTIPLIER ** max(0, MAX_AUTO_RESTARTS - 1))
)
RESTART_CAP_RESET_SECONDS = _env_int(
    "HEALTH_RESTART_CAP_RESET_SECONDS",
    max(1800, _MAX_COOLDOWN_SECONDS),
    minimum=1,
)
STATE_TTL_SECONDS = max(3600, _MAX_COOLDOWN_SECONDS * 4)


def _failure_key(service_id: str) -> str:
    return f"health:fail:{service_id}"


def _restart_key(service_id: str) -> str:
    return f"health:restart:{service_id}"


def _last_check_key(service_id: str) -> str:
    return f"health:last-check:{service_id}"


def _container_state_key(service_id: str) -> str:
    return f"health:state:{service_id}"


def _clear_state(service_id: str, clear_restart: bool = False):
    cache.delete(_failure_key(service_id))
    if clear_restart:
        cache.delete(_restart_key(service_id))
    cache.delete(_last_check_key(service_id))
    cache.delete(_container_state_key(service_id))


def _normalize_path(path: str) -> str:
    path = (path or "/").strip()
    if not path:
        path = "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _candidate_health_paths(service) -> list[str]:
    paths = []
    seen = set()

    def _add(path_value: str):
        path = _normalize_path(path_value)
        if path in seen:
            return
        seen.add(path)
        paths.append(path)

    _add(service.health_check_path or "/")
    raw = os.environ.get(
        "HEALTH_CHECK_FALLBACK_PATHS",
        "/,/health,/healthz,/ready,/live,/status",
    )
    for chunk in str(raw).split(","):
        chunk = chunk.strip()
        if chunk:
            _add(chunk)

    return paths or ["/"]


def _candidate_ports(service) -> list[int]:
    ports = []
    seen = set()

    def _add(value):
        try:
            port = int(value)
        except (TypeError, ValueError):
            return
        if port <= 0 or port in seen:
            return
        seen.add(port)
        ports.append(port)

    _add(getattr(service, "health_check_port", None))
    _add(getattr(service, "internal_port", None))

    try:
        port_var = service.env_vars.filter(key="PORT").order_by("-updated_at").first()
        if port_var is not None:
            _add(port_var.value)
    except Exception:
        pass

    raw = os.environ.get("HEALTH_CHECK_FALLBACK_PORTS", "8000,3000,8080,5000")
    for chunk in str(raw).split(","):
        _add(chunk.strip())

    return ports or [8000]


def _should_verify_tls() -> bool:
    raw = str(os.environ.get("HEALTH_CHECK_VERIFY_TLS", "true")).strip().lower()
    return raw not in ("0", "false", "no")


def _server_verify_tls(server) -> bool:
    """SECURITY (Issue 78): respect the per-server ``verify_tls`` flag
    when probing the internal/private/mesh/container targets.  The
    platform defaults to True so that we never silently skip cert
    verification unless the operator has explicitly opted in on a
    given server (``ManagedServer.verify_tls=False``).
    """
    return bool(getattr(server, "verify_tls", True))


def _platform_ssl_enabled() -> bool:
    try:
        from apps.deployments.models import PlatformConfig

        return bool(PlatformConfig.load().use_ssl)
    except Exception:
        # If config cannot load (migrations/startup), default to HTTPS.
        return True


def _check_due(service) -> bool:
    interval = max(10, int(service.health_check_interval or 60))
    now = time.time()
    key = _last_check_key(str(service.id))
    last_check = cache.get(key)
    if last_check is not None:
        try:
            if (now - float(last_check)) < interval:
                return False
        except (TypeError, ValueError):
            pass

    cache.set(key, now, timeout=max(3600, interval * 4))
    return True


def _build_targets(service, active_deployment):
    paths = _candidate_health_paths(service)
    ports = _candidate_ports(service)
    targets = []
    seen = set()

    def _add(url: str, headers=None, verify=True):
        header_host = (headers or {}).get("Host", "")
        key = (url, header_host)
        if key in seen:
            return
        seen.add(key)
        targets.append({"url": url, "headers": headers or {}, "verify": verify})

    public_domain = ""
    if not getattr(service, "public_domain_hidden", False):
        public_domain = (service.public_domain or "").strip()
    if public_domain:
        scheme = "https" if _platform_ssl_enabled() else "http"
        verify = _should_verify_tls() if scheme == "https" else True
        for path in paths:
            _add(f"{scheme}://{public_domain}{path}", verify=verify)

        # Internal fallback path avoids DNS/TLS propagation noise.
        internal_urls = []
        configured = os.environ.get("TRAEFIK_INTERNAL_URL", "").strip()
        if configured:
            internal_urls.append(configured.rstrip("/"))
        internal_urls.append("http://traefik:80")
        is_lite = getattr(service.server, "is_lite_agent", False) if service.server else False
        if is_lite:
            internal_urls.extend(
                [
                    "http://127.0.0.1:80",
                    "http://localhost:80",
                ]
            )
        else:
            internal_urls.extend(
                [
                    "http://127.0.0.1:8081",
                    "http://localhost:8081",
                ]
            )
        for base_url in internal_urls:
            for path in paths:
                _probe_url = f"{base_url}{path}"
                _add(
                    _probe_url,
                    headers={"Host": public_domain},
                    verify=should_verify(_probe_url),
                )

    # ── Mesh & Private IP Targets (AWS/VPN Optimization) ────────────────
    from apps.deployments.models_mesh import WireGuardPeer

    # SECURITY (Issue 78): respect the per-server ``verify_tls`` flag
    # even on the plain-HTTP internal probes. Previously the code
    # hard-coded ``verify=False`` for these URLs, which would have
    # silently disabled TLS verification had the scheme ever changed.

    # 1. Private IP (Internal Cloud Network)
    if getattr(service.server, 'private_ip', None):
        p_ip = service.server.private_ip
        verify_private = _server_verify_tls(service.server)
        for port in ports:
            for path in paths:
                _add(f"http://{p_ip}:{port}{path}", verify=verify_private)

    # 2. Mesh IP (WireGuard VPN Network)
    # Find this server's mesh IP
    if service.server:
        mesh_peer = WireGuardPeer.objects.filter(server=service.server, is_active=True).first()
        if mesh_peer and mesh_peer.wg_address:
            m_ip = mesh_peer.wg_address
            verify_mesh = _server_verify_tls(service.server)
            for port in ports:
                for path in paths:
                    _add(f"http://{m_ip}:{port}{path}", verify=verify_mesh)

    # ── Container-local Targets (Docker DNS) ───────────────────────────
    # Use the service name as Docker DNS hostname
    container_id = (active_deployment.container_id or "").strip()
    if container_id:
        direct_headers = {"Host": public_domain} if public_domain else {}

        service_name = (service.name or "").strip()
        if service_name:
            for port in ports:
                for path in paths:
                    _probe_url = f"http://{service_name}:{port}{path}"
                    _add(
                        _probe_url,
                        headers=direct_headers,
                        verify=should_verify(_probe_url),
                    )
    return targets


def _service_startup_grace_seconds(service) -> int:
    grace = STARTUP_GRACE_SECONDS

    try:
        cpu_cores = float(service.cpu_cores or 0)
    except (TypeError, ValueError):
        cpu_cores = 0.0

    try:
        memory_mb = int(service.memory_mb or 0)
    except (TypeError, ValueError):
        memory_mb = 0

    is_low_resource = (
        (cpu_cores > 0 and cpu_cores <= LOW_RESOURCE_CPU_THRESHOLD)
        or (memory_mb > 0 and memory_mb <= LOW_RESOURCE_MEMORY_THRESHOLD_MB)
    )
    if is_low_resource:
        grace += LOW_RESOURCE_EXTRA_GRACE_SECONDS
    return max(0, grace)


def _check_infrastructure_health():
    """Check Falco, fail2ban, and container runtime health, log warnings if down."""
    import subprocess

    # Container runtime (gVisor / Kata / runc)
    runtime_cache_key = "infra:runtime:last_detected"
    try:
        from apps.deployments.services.container_runtime import detect_best_runtime, is_sandboxed_runtime
        runtime = detect_best_runtime()
        last = cache.get(runtime_cache_key)
        if last and last != runtime:
            logger.warning("Container runtime changed: %s -> %s", last, runtime)
        cache.set(runtime_cache_key, runtime, timeout=86400)
        if runtime == "runsc":
            logger.info("Container runtime: gVisor (runsc) — sandboxed")
        elif runtime == "kata-runtime":
            logger.info("Container runtime: Kata Containers — VM-level isolation")
        elif not is_sandboxed_runtime(runtime):
            # Only warn if sandboxed was expected but unavailable
            sandbox_env = os.environ.get("SMSLY_CONTAINER_RUNTIME", "").strip().lower()
            if sandbox_env in ("runsc", "gvisor", "kata", "kata-runtime"):
                logger.warning("Sandboxed runtime %s requested but unavailable, using runc", sandbox_env)
    except Exception as exc:
        logger.debug("Runtime health check skipped: %s", exc)

    # Falco
    falco_cache_key = "infra:falco:down_count"
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=smsly-falco",
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if "Up" not in (result.stdout or ""):
            down_count = (cache.get(falco_cache_key) or 0) + 1
            cache.set(falco_cache_key, down_count, timeout=3600)
            if down_count <= 3 or down_count % 10 == 0:
                logger.warning("Falco container is not running (detected %d times)", down_count)
        else:
            cache.delete(falco_cache_key)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("Falco health check skipped: %s", exc)

    # fail2ban
    f2b_cache_key = "infra:fail2ban:down_count"
    try:
        result = subprocess.run(
            ["fail2ban-client", "ping"],
            capture_output=True, text=True, timeout=5,
        )
        if "pong" not in (result.stdout or ""):
            down_count = (cache.get(f2b_cache_key) or 0) + 1
            cache.set(f2b_cache_key, down_count, timeout=3600)
            if down_count <= 3 or down_count % 10 == 0:
                logger.warning("fail2ban is not responding (detected %d times)", down_count)
        else:
            cache.delete(f2b_cache_key)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("fail2ban health check skipped: %s", exc)


@shared_task
def monitor_health_task():
    """
    Check health for all services with configured health paths.

    Uses per-service interval gating to avoid over-checking.
    """
    from apps.deployments.models import Deployment, Service

    services = Service.objects.exclude(health_check_path="")
    checked = 0
    skipped = 0

    for service in services:
        try:
            if not _check_due(service):
                skipped += 1
                continue
            _check_service_health(service, Deployment)
            checked += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Health check failed for %s: %s", service.name, exc)

    logger.info("Health monitor checked=%d skipped=%d", checked, skipped)

    # ── Infrastructure health (Falco + fail2ban) ──
    _check_infrastructure_health()



def _probe_container_state(container_id: str) -> tuple[str, int | None]:
    """Fetch container status and exit code from Docker.

    Returns (status: str, exit_code: int | None).
    Status is one of: created, restarting, running, removing, paused,
    exited, dead.  Returns ("unknown", None) if Docker is unreachable
    or the container no longer exists.
    """
    import docker

    if not container_id:
        return ("unknown", None)
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        state = container.attrs.get("State", {})
        status = (state.get("Status") or "unknown").lower()
        exit_code = state.get("ExitCode")
        return (status, exit_code)
    except docker.errors.NotFound:
        return ("not-found", None)
    except Exception:
        return ("unknown", None)


def _check_service_health(service, Deployment):
    """Check a single service's health and update service status."""
    active = (
        Deployment.objects.filter(service=service, status=Deployment.Status.ACTIVE)
        .order_by("-created_at")
        .first()
    )
    service_key = str(service.id)

    if not active:
        if service.health_status != "unknown":
            service.health_status = "unknown"
            service.save(update_fields=["health_status", "updated_at"])
        _clear_state(service_key, clear_restart=True)
        return

    # Give fresh deployments a warm-up period before failing them.
    startup_grace_seconds = _service_startup_grace_seconds(service)
    if startup_grace_seconds > 0 and active.created_at:
        age = (timezone.now() - active.created_at).total_seconds()
        if age < startup_grace_seconds:
            return

    # Skip health check if a manual restart just happened (grace period)
    if cache.get(f"health:restart_grace:{service.id}"):
        return

    # Check Docker container state: if the container is dead, fast-fail.
    container_id = (active.container_id or "").strip()
    state, exit_code = _probe_container_state(container_id)
    is_crashed = state in {"exited", "dead", "not-found", "restarting"}

    cache_key = _container_state_key(service_key)
    if is_crashed:
        cache.set(cache_key, {"state": state, "exit_code": exit_code}, timeout=STATE_TTL_SECONDS)
    else:
        cache.delete(cache_key)

    targets = _build_targets(service, active)
    if not targets:
        return

    timeout = max(2, int(service.health_check_timeout or 15))
    failure_reason = "Health target not reachable"

    for target in targets:
        url = target["url"]
        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers=target["headers"],
                verify=target["verify"],
                allow_redirects=False,
            )
            if 200 <= response.status_code < 400:
                cache.delete(_failure_key(service_key))
                restart_state = cache.get(_restart_key(service_key))
                if restart_state:
                    logger.info(
                        "%s recovered; clearing restart state (previous attempts: %s)",
                        service.name,
                        restart_state.get("count", 0),
                    )
                    cache.delete(_restart_key(service_key))
                if service.health_status != "healthy":
                    log_event(
                        action="SERVICE_HEALTHY",
                        target=f"Service: {service.name}",
                        metadata={
                            "url": url,
                            "latency_ms": response.elapsed.total_seconds() * 1000,
                            "status_code": response.status_code,
                            "previous_status": service.health_status
                        }
                    )
                    service.health_status = "healthy"
                    service.save(update_fields=["health_status", "updated_at"])
                return
            failure_reason = f"{url} returned HTTP {response.status_code}"
        except requests.Timeout:
            failure_reason = f"{url} timed out"
        except requests.exceptions.SSLError as exc:
            failure_reason = f"{url} TLS error: {exc}"
        except requests.RequestException as exc:
            failure_reason = f"{url} request failed: {exc}"

    _handle_failure(service, service_key, failure_reason)


def _handle_failure(service, service_key: str, reason: str):
    """Handle failed health checks and trigger guarded auto-restart."""
    failure_key = _failure_key(service_key)
    current_failures = int(cache.get(failure_key, 0) or 0) + 1
    cache.set(failure_key, current_failures, timeout=STATE_TTL_SECONDS)

    # Use fast retries if the container is dead (exited/crashed/not-found),
    # otherwise use the service's configured HTTP retry threshold.
    state_info = cache.get(_container_state_key(service_key))
    if state_info and isinstance(state_info, dict):
        retries = max(2, CRASH_FAST_RETRIES)
    else:
        retries = max(2, int(service.health_check_retries or 8))

    logger.warning(
        "%s health check failed (%d/%d): %s",
        service.name,
        current_failures,
        retries,
        reason,
    )

    if current_failures < retries:
        return

    if service.health_status != "unhealthy":
        log_event(
            action="SERVICE_UNHEALTHY",
            target=f"Service: {service.name}",
            metadata={
                "reason": reason,
                "consecutive_failures": current_failures,
                "threshold": retries,
                "auto_restart": service.auto_restart
            }
        )
        service.health_status = "unhealthy"
        service.save(update_fields=["health_status", "updated_at"])

    if not service.auto_restart:
        return

    if not _should_restart(service, service_key):
        return

    if _trigger_restart(service, service_key):
        # After scheduling a restart, reset the failure counter for fresh retries.
        cache.delete(failure_key)


def _should_restart(service, service_key: str) -> bool:
    """
    Restart gate with exponential backoff and max attempt cap.
    """
    state = cache.get(_restart_key(service_key)) or {}
    restart_count = int(state.get("count", 0) or 0)
    last_restart = float(state.get("last_restart", 0) or 0)

    if restart_count >= MAX_AUTO_RESTARTS:
        elapsed_since_last = max(0.0, time.time() - last_restart)
        if last_restart and elapsed_since_last >= RESTART_CAP_RESET_SECONDS:
            logger.info(
                "%s restart cap window elapsed (%ds); resetting restart counter.",
                service.name,
                int(elapsed_since_last),
            )
            cache.delete(_restart_key(service_key))
            restart_count = 0
            last_restart = 0.0
        else:
            logger.warning(
                "%s auto-restart cap reached (%d/%d). Manual intervention required.",
                service.name,
                restart_count,
                MAX_AUTO_RESTARTS,
            )
            return False

    if restart_count == 0:
        return True

    cooldown = RESTART_COOLDOWN_BASE * (BACKOFF_MULTIPLIER ** (restart_count - 1))
    elapsed = max(0.0, time.time() - last_restart)
    if elapsed < cooldown:
        remaining = int(cooldown - elapsed)
        logger.info(
            "%s restart cooldown active: %ds remaining (attempt %d/%d).",
            service.name,
            remaining,
            restart_count,
            MAX_AUTO_RESTARTS,
        )
        return False

    return True


def _record_restart_attempt(service_key: str):
    state = cache.get(_restart_key(service_key)) or {}
    count = int(state.get("count", 0) or 0) + 1
    cache.set(
        _restart_key(service_key),
        {**state, "count": count, "last_restart": time.time()},
        timeout=STATE_TTL_SECONDS,
    )


def _trigger_restart(service, service_key: str) -> bool:
    """Queue a deployment restart if safe to do so."""
    try:
        from apps.cloud.models import CloudProvider
        from apps.deployments.models import Deployment
        from apps.deployments.tasks_deploy import enqueue_smart_deploy_task

        # Do not stack restarts while any deployment for this service is in flight.
        in_flight_statuses = [
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        ]
        if Deployment.objects.filter(service=service, status__in=in_flight_statuses).exists():
            logger.info("Skipping auto-restart for %s: deployment already in progress.", service.name)
            return False

        # Don't pile on if a deployment recently failed — the issue
        # is likely systemic and auto-restart won't help.
        from datetime import timedelta
        if Deployment.objects.filter(
            service=service,
            status=Deployment.Status.FAILED,
            finished_at__gte=timezone.now() - timedelta(seconds=RESTART_COOLDOWN_BASE),
        ).exists():
            logger.info(
                "Skipping auto-restart for %s: recent deployment failure within cooldown.",
                service.name,
            )
            return False

        # Guard: don't auto-restart if recent deployments keep failing.
        # This prevents restart storms when the issue is persistent
        # (bad code, missing env var, broken Dockerfile, etc.).
        # The window/threshold matches ``AutoRollbackEngine`` so the
        # fast-path here is a thin shim around the engine's check.
        from apps.deployments.services.auto_rollback import (
            AUTO_ROLLBACK_THRESHOLD,
            AUTO_ROLLBACK_WINDOW_MINUTES,
        )
        threshold = (
            getattr(service, 'auto_rollback_threshold', None)
            or AUTO_ROLLBACK_THRESHOLD
        )
        recent_failures = Deployment.objects.filter(
            service=service,
            status=Deployment.Status.FAILED,
            is_rollback=False,
            created_at__gte=(
                timezone.now()
                - timedelta(minutes=AUTO_ROLLBACK_WINDOW_MINUTES)
            ),
        ).count()
        if recent_failures >= threshold:
            logger.warning(
                "Skipping auto-restart for %s: %d deployments failed in the last "
                "%d min. Triggering auto-rollback instead.",
                service.name, recent_failures, AUTO_ROLLBACK_WINDOW_MINUTES,
            )
            from apps.deployments.services.auto_rollback import (
                AutoRollbackEngine,
                Trigger,
            )
            AutoRollbackEngine.trigger(
                service=service,
                trigger=Trigger.HEALTH_CHECK_FALLBACK,
                reason_detail=(
                    f"{recent_failures} deployments failed in the last hour; "
                    f"health check restart aborted"
                ),
            )
            service.health_status = "needs_manual_intervention"
            service.save(update_fields=["health_status", "updated_at"])
            return False

        latest = Deployment.objects.filter(service=service).order_by("-created_at").first()
        if not latest:
            logger.warning("Skipping auto-restart for %s: no prior deployment found.", service.name)
            return False

        state = cache.get(_restart_key(service_key)) or {}
        last_fallback_id = state.get("last_fallback_deployment_id")
        if last_fallback_id:
            last_fallback = Deployment.objects.filter(id=last_fallback_id).first()
            if last_fallback and last_fallback.status == Deployment.Status.FAILED:
                logger.warning(
                    "Auto-restart skipped for %s: previous fallback deployment %s failed.",
                    service.name, last_fallback_id,
                )
                if service.health_status != "needs_manual_intervention":
                    service.health_status = "needs_manual_intervention"
                    service.save(update_fields=["health_status", "updated_at"])
                return False

        successful = (
            Deployment.objects
            .filter(service=service, status=Deployment.Status.ACTIVE)
            .order_by("-finished_at")
            .first()
        )
        fallback = successful.commit_hash if successful else None
        if not fallback:
            logger.warning(
                "Auto-restart skipped for service %s: no successful deployment to fall back to.",
                service.id,
            )
            if service.health_status != "needs_manual_intervention":
                service.health_status = "needs_manual_intervention"
                service.save(update_fields=["health_status", "updated_at"])
            return False

        provider = service.provider or CloudProvider.objects.filter(is_active=True).first()
        if not provider:
            logger.warning("Skipping auto-restart for %s: no active cloud provider.", service.name)
            return False

        new_deployment = Deployment.objects.create(
            service=service,
            commit_hash=fallback,
            status=Deployment.Status.QUEUED,
            commit_message="Auto-restart after health check failure (fallback to last successful commit)",
        )
        service.health_status = "starting"
        service.save(update_fields=["health_status", "updated_at"])

        try:
            enqueue_smart_deploy_task(str(new_deployment.id), str(provider.id), skip_review=True)
        except Exception as exc:  # pragma: no cover - broker/runtime failure
            logger.exception(
                "Failed to enqueue auto-restart deployment %s",
                new_deployment.id,
            )
            new_deployment.status = Deployment.Status.FAILED
            new_deployment.finished_at = timezone.now()
            new_deployment.build_logs = (
                (new_deployment.build_logs or "")
                + f"\n[ERROR] Failed to queue auto-restart task: {exc}\n"
            )
            new_deployment.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
            # Clear the restart state so the next failure can retry
            # instead of permanently blocking auto-restart.
            cache.delete(_restart_key(service_key))
            return False

        # Only record last_fallback_deployment_id AFTER successful enqueue.
        cache.set(
            _restart_key(service_key),
            {**(state or {}), "last_fallback_deployment_id": str(new_deployment.id)},
            timeout=STATE_TTL_SECONDS,
        )
        _record_restart_attempt(service_key)

        state = cache.get(_restart_key(service_key)) or {}
        logger.info(
            "Auto-restart queued for %s (attempt %s/%d).",
            service.name,
            state.get("count", 1),
            MAX_AUTO_RESTARTS,
        )
        return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Auto-restart failed for %s: %s", service.name, exc)
        return False


def reset_restart_state(service_id: str):
    """
    Clear restart/failure state.
    Call on manual restart/deploy so backoff does not linger.
    """
    key = str(service_id)
    _clear_state(key, clear_restart=True)
    logger.info("Restart state cleared for service %s", service_id)
