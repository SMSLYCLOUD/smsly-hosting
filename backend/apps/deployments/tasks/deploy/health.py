"""Health check and route readiness helper functions."""
import logging
import os
import time

import docker
import requests

from apps.deployments.models import Service
from apps.deployments.services.tls_verify import should_verify
from apps.deployments.utils import append_log

from .helpers import _env_int

logger = logging.getLogger(__name__)
def _is_traefik_not_ready(response: requests.Response) -> bool:
    """
    Detect Traefik's default no-route 404 response.

    In production this response may not include a `Server: traefik` header,
    so rely on the canonical body + headers rather than `Server` only.
    """
    body = (response.text or "").strip().lower()
    if response.status_code != 404 or body != "404 page not found":
        return False

    content_type = (response.headers.get("Content-Type") or "").lower()
    nosniff = (response.headers.get("X-Content-Type-Options") or "").lower()
    return content_type.startswith("text/plain") and nosniff == "nosniff"

def _route_misroute_reason(response: requests.Response) -> str:
    """
    Detect responses that prove a service hostname hit platform fallback.

    A deployed service route is not ready if it returns the control-plane
    frontend or the route-fallback page, even if the HTTP status is otherwise
    successful.
    """
    control_plane = str(response.headers.get("X-SMSLY-Control-Plane", "")).strip().lower()
    if control_plane in {"1", "true", "yes"}:
        return "service hostname reached the control-plane proxy"

    route_fallback = str(response.headers.get("X-SMSLY-Route-Fallback", "")).strip().lower()
    if route_fallback in {"1", "true", "yes"}:
        return "service hostname reached the route fallback page"

    body = (response.text or "")[:12000].lower()
    fallback_markers = (
        "grid routing",
        "service is waking up",
        "automatically reconnect traffic",
    )
    if any(marker in body for marker in fallback_markers):
        return "service hostname rendered the route fallback page"

    platform_markers = (
        "the sovereign paas",
        "deployment previews",
        "global edge routing",
        "connect your own vps",
    )
    if sum(1 for marker in platform_markers if marker in body) >= 2:
        return "service hostname rendered the platform homepage"

    return ""

def _is_low_resource_service(service: Service) -> bool:
    try:
        cpu_threshold = float(os.environ.get("LOW_RESOURCE_CPU_CORES_THRESHOLD", "0.75"))
    except (TypeError, ValueError):
        cpu_threshold = 0.75
    memory_threshold = _env_int("LOW_RESOURCE_MEMORY_MB_THRESHOLD", 768, minimum=64)

    try:
        cpu_cores = float(service.cpu_cores or 0)
    except (TypeError, ValueError):
        cpu_cores = 0.0

    try:
        memory_mb = int(service.memory_mb or 0)
    except (TypeError, ValueError):
        memory_mb = 0

    return (
        (cpu_cores > 0 and cpu_cores <= cpu_threshold)
        or (memory_mb > 0 and memory_mb <= memory_threshold)
    )

def _local_route_timeout_seconds(service: Service) -> int:
    if _is_low_resource_service(service):
        return _env_int(
            "LOCAL_ROUTE_READY_TIMEOUT_LOW_RESOURCE_SECONDS",
            120,
            minimum=10,
        )
    return _env_int("LOCAL_ROUTE_READY_TIMEOUT_SECONDS", 60, minimum=10)

def _local_container_timeout_seconds(service: Service) -> int:
    if _is_low_resource_service(service):
        return _env_int(
            "LOCAL_CONTAINER_HEALTH_TIMEOUT_LOW_RESOURCE_SECONDS",
            600,
            minimum=60,
        )
    return _env_int("LOCAL_CONTAINER_HEALTH_TIMEOUT_SECONDS", 480, minimum=60)

def _wait_for_local_container_healthy(
    deployment,
    container_id: str,
    timeout_seconds: int = 480,
    poll_seconds: int = 5,
) -> bool:
    """
    Wait for a freshly deployed local container to be healthy/running.

    This prevents deployments from being marked ACTIVE when the container
    immediately crash-loops or fails its Docker health check.
    """
    try:
        # Check if docker is available (it should be, imported at top level)
        pass
    except Exception:  # pragma: no cover - import failure is environment-specific
        append_log(
            deployment,
            "[HEALTH-CHECK] Docker SDK unavailable; skipping container health wait.\n",
        )
        return False

    try:
        client = docker.from_env()
    except Exception as exc:  # pragma: no cover - daemon/socket issues are environment-specific
        append_log(
            deployment,
            f"[HEALTH-CHECK] Docker client unavailable ({exc}); skipping container health wait.\n",
        )
        return False

    deadline = time.monotonic() + timeout_seconds
    last_state = "unknown"
    while time.monotonic() < deadline:
        try:
            container = client.containers.get(container_id)
            container.reload()
            state = container.attrs.get("State") or {}
            status = (state.get("Status") or "").lower()
            health = ((state.get("Health") or {}).get("Status") or "").lower()
            last_state = f"status={status or 'unknown'}, health={health or 'n/a'}"
        except Exception as exc:  # pragma: no cover - container lookups are runtime-dependent
            last_state = f"lookup_error={exc}"
            time.sleep(poll_seconds)
            continue

        if status in {"exited", "dead"}:
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container terminated early ({last_state}).\n",
            )
            return False

        if health == "healthy":
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container healthy ({last_state}).\n",
            )
            return True

        if health == "unhealthy":
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container unhealthy ({last_state}).\n",
            )
            return False

        # Still within Docker health check start_period — keep polling
        if health == "starting":
            append_log(
                deployment,
                f"[HEALTH-CHECK] Health check still in start_period ({last_state}).\n",
            )
            time.sleep(poll_seconds)
            continue

        # No Docker healthcheck configured; consider running container ready.
        if status == "running" and not health:
            append_log(
                deployment,
                f"[HEALTH-CHECK] Container running without healthcheck ({last_state}).\n",
            )
            return True

        time.sleep(poll_seconds)

    # If the container is running but health is still "starting" (Docker
    # start_period hasn't expired yet), treat it as healthy — the app is
    # up and serving even though Docker hasn't finished its first probe.
    if status in ("running",) and health in ("starting", "n/a", ""):
        append_log(
            deployment,
            f"[HEALTH-CHECK] Container running; health still in start_period "
            f"({last_state}). Accepting as healthy.\n",
        )
        return True
    append_log(
        deployment,
        f"[HEALTH-CHECK] Timed out waiting for container health ({last_state}).\n",
    )
    return False

def _wait_for_local_route_ready(
    deployment,
    service,
    timeout_seconds: int = 0,
    poll_seconds: int = 3,
) -> bool:
    """
    Wait until Traefik has picked up host routing for this service.

    If timeout_seconds <= 0, polls indefinitely (capped by Celery task timeout).
    """
    host = (service.public_domain or "").strip()
    if not host:
        return True

    # Probe through the public edge first, then the raw Traefik ingress. The
    # direct Traefik probe is useful during DNS propagation, but it must not
    # mask a Caddy misroute that serves the platform homepage.
    probe_candidates: list = []    # type: ignore[var-annotated]

    def _add_probe(base_url: str, headers: dict | None = None, verify: bool = True, kind: str = "direct"):
        normalized = (base_url or "").rstrip("/")
        if not normalized:
            return
        probe_candidates.append(
            {
                "base_url": normalized,
                "headers": headers or {},
                "verify": verify,
                "kind": kind,
            }
        )

    _add_probe(f"https://{host}", verify=True, kind="edge")
    _add_probe(f"http://{host}", verify=True, kind="edge")
    _add_probe(
        "http://caddy:80",
        headers={"Host": host},
        verify=should_verify("http://caddy:80"),
        kind="edge",
    )
    configured = os.environ.get("TRAEFIK_INTERNAL_URL", "").strip()
    if configured:
        _add_probe(
            configured,
            headers={"Host": host},
            verify=should_verify(configured),
        )
    _add_probe(
        "http://traefik:80",
        headers={"Host": host},
        verify=should_verify("http://traefik:80"),
    )

    is_lite = getattr(service.server, "is_lite_agent", False) if service.server else False
    if is_lite:
        _add_probe(
            "http://127.0.0.1:80",
            headers={"Host": host},
            verify=should_verify("http://127.0.0.1:80"),
        )
        _add_probe(
            "http://localhost:80",
            headers={"Host": host},
            verify=should_verify("http://localhost:80"),
        )
    else:
        _add_probe(
            "http://127.0.0.1:8081",
            headers={"Host": host},
            verify=should_verify("http://127.0.0.1:8081"),
        )
        _add_probe(
            "http://localhost:8081",
            headers={"Host": host},
            verify=should_verify("http://localhost:8081"),
        )

    # Preserve order and remove duplicates.
    probes = []
    seen = set()
    for probe in probe_candidates:
        key = (probe["base_url"], tuple(sorted(probe["headers"].items())))
        if key in seen:
            continue
        seen.add(key)
        probes.append(probe)

    path_candidates = []
    if service.health_check_path:
        path_candidates.append(service.health_check_path)
    path_candidates.extend(["/", "/health", "/healthz"])

    paths = []
    seen_paths = set()
    for raw_path in path_candidates:
        path = raw_path if str(raw_path).startswith("/") else f"/{raw_path}"
        if path not in seen_paths:
            seen_paths.add(path)
            paths.append(path)

    use_deadline = timeout_seconds > 0
    append_log(
        deployment,
        f"[ROUTE-CHECK] Polling route for host {host} "
        f"({'until active' if not use_deadline else f'timeout {timeout_seconds}s'})\n",
    )

    deadline = time.monotonic() + timeout_seconds if use_deadline else 0
    last_error = ""
    edge_misroute_seen = False
    attempt = 0
    while True:
        if use_deadline and time.monotonic() > deadline:
            break
        attempt += 1
        for probe in probes:
            base_url = probe["base_url"]
            for path in paths:
                url = f"{base_url}{path}"
                try:
                    response = requests.get(
                        url,
                        headers=probe["headers"],  # type: ignore[arg-type]
                        timeout=(
                            _env_int("LOCAL_ROUTE_EDGE_PROBE_TIMEOUT_SECONDS", 4, minimum=1)
                            if probe.get("kind") == "edge"
                            else 8
                        ),
                        verify=probe["verify"],  # type: ignore[arg-type]
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    last_error = f"{url}: {exc}"
                    continue

                misroute_reason = _route_misroute_reason(response)
                if misroute_reason:
                    last_error = f"{url}: {misroute_reason}"
                    if probe.get("kind") == "edge":
                        edge_misroute_seen = True
                    continue

                if probe.get("kind") == "edge" and 300 <= response.status_code < 400:
                    location = response.headers.get("Location", "")
                    last_error = f"{url}: edge redirect {response.status_code} to {location or 'unknown'}"
                    continue

                if response.status_code >= 500:
                    last_error = f"{url}: HTTP {response.status_code}"
                    continue

                # Traefik can briefly return default 404 while labels propagate.
                if _is_traefik_not_ready(response):
                    last_error = f"{url}: Traefik route not ready yet"
                    continue

                if probe.get("kind") == "direct" and edge_misroute_seen:
                    last_error = (
                        f"{url}: direct Traefik route is active, but edge route "
                        "is still hitting the platform fallback"
                    )
                    continue

                append_log(
                    deployment,
                    f"[ROUTE-CHECK] Route active via {url} "
                    f"(HTTP {response.status_code}, attempt {attempt})\n",
                )
                return True

        time.sleep(poll_seconds)

    append_log(
        deployment,
        "[ROUTE-CHECK] Routing readiness timed out. "
        f"Last error: {last_error or 'unknown'}\n",
    )
    return False
