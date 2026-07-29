#!/usr/bin/env python3
"""
SMSLY Agent Registrar — lite-agent self-registration service.

A small long-running service that lives inside the lite-agent
docker-compose stack. Its job is to make the agent's boot
state visible to the master without requiring an operator to
click anything.

Three responsibilities:

1. **Self-registration (one-shot)**
   On startup, POST to ``/api/v1/servers/{id}/agent-ready/`` on
   the master. The endpoint is HMAC-authenticated with the
   per-server ``gateway_secret`` (see
   ``services/agent_registrar_auth.py`` on the master side).
   This is what flips the dashboard's "Ready" indicator from
   grey to green.

2. **Heartbeats (every 30s)**
   Periodically POST a runtime snapshot to
   ``/api/v1/servers/{id}/agent-heartbeat/`` so the master can
   detect silent agent outages. The snapshot includes docker
   version, smsly image versions, host uptime, disk/mem
   percent, and the agent's self-reported health string.

3. **Retry with exponential backoff**
   If the master is unreachable (e.g. WireGuard is still
   coming up, or the master is mid-restart), the registrar
   retries with exponential backoff (1s → 2s → 4s → ... up to
   60s). It never gives up. The whole point is to be
   persistent.

Configuration is via environment variables (set in
``docker-compose.agent-lite.yml``):

* ``MASTER_API_URL``        — base URL of the master's API
                              (``http://10.100.0.1`` for the
                              mesh, or ``http://<public_ip>``
                              as fallback)
* ``MASTER_API_URL_FALLBACK`` — comma-separated list of
                              additional base URLs to try in
                              order (mesh → public)
* ``SERVER_ID``             — UUID of the ManagedServer row
* ``GATEWAY_SECRET``        — per-server HMAC secret
* ``HEARTBEAT_INTERVAL``    — seconds between heartbeats
                              (default 30)
* ``REGISTER_ON_START``     — set to ``false`` to skip the
                              one-shot agent-ready call
* ``LOG_LEVEL``             — DEBUG/INFO/WARNING/ERROR

The registrar never crashes the host. Any exception is logged
and the loop continues. If the docker daemon is unavailable,
the snapshot is replaced with a minimal payload so the master
still gets *some* signal that the agent is alive.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any

# ── Configuration ─────────────────────────────────────────────────────────
MASTER_API_URL = os.environ.get("MASTER_API_URL", "").strip()
FALLBACK_URLS = [
    u.strip() for u in os.environ.get("MASTER_API_URL_FALLBACK", "").split(",")
    if u.strip()
]
SERVER_ID = os.environ.get("SERVER_ID", "").strip()
GATEWAY_SECRET = os.environ.get("GATEWAY_SECRET", "").strip()
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
REGISTER_ON_START = os.environ.get("REGISTER_ON_START", "true").lower() not in {
    "false", "0", "no", "off",
}
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Compose a single ordered list of base URLs to try, with the primary
# first and the fallbacks after. The first one that responds is used
# for all subsequent calls.
ALL_BASE_URLS = []
if MASTER_API_URL:
    ALL_BASE_URLS.append(MASTER_API_URL)
for fb in FALLBACK_URLS:
    if fb not in ALL_BASE_URLS:
        ALL_BASE_URLS.append(fb)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [registrar] %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("agent-registrar")


# ── HMAC signing ──────────────────────────────────────────────────────────
def sign(method: str, full_path: str, body: bytes) -> dict[str, str]:
    """Return the HMAC V2 headers for a request.

    The format matches the master's
    ``services/agent_registrar_auth.compute_agent_hmac``:
        {method}|{full_path}|{ts}|{nonce}|{body_sha256}
    """
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_hash = hashlib.sha256(body or b"").hexdigest()
    payload = f"{method.upper()}|{full_path}|{ts}|{nonce}|{body_hash}"
    sig = hmac.new(
        GATEWAY_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Gateway-Signature-V2": sig,
        "X-Request-Timestamp": ts,
        "X-Request-Nonce": nonce,
        "Content-Type": "application/json",
    }


# ── HTTP helpers ──────────────────────────────────────────────────────────
def _post_json(base_url: str, path: str, body: dict, timeout: int = 10) -> tuple[int, str]:
    body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
    url = base_url.rstrip("/") + path
    headers = sign("POST", path, body_bytes)
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else ""
    except urllib.error.URLError as e:
        return 0, f"URLError: {e.reason}"
    except Exception as e:
        return 0, f"Exception: {e!r}"


# ── Runtime snapshot ─────────────────────────────────────────────────────
_docker_version_cache: tuple[float, str] = (0.0, "")


def _read_docker_version() -> str:
    """Return the docker daemon version. Cache for 5 minutes."""
    global _docker_version_cache
    now = time.monotonic()
    if now - _docker_version_cache[0] < 300 and _docker_version_cache[1]:
        return _docker_version_cache[1]
    try:
        out = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            _docker_version_cache = (now, out.stdout.strip())
            return _docker_version_cache[1]
    except Exception:
        pass
    return ""


def _read_smsly_images() -> list[dict[str, str]]:
    """List the locally-built smsly image tags and their IDs."""
    try:
        out = subprocess.run(
            ["docker", "images", "--format",
             "{{.Repository}}|{{.Tag}}|{{.ID}}|{{.Size}}",
             "--filter", "label=com.smsly.component"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return []
        images = []
        for line in out.stdout.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 4:
                images.append({
                    "repo": parts[0],
                    "tag": parts[1],
                    "id": parts[2],
                    "size": parts[3],
                })
        return images[:30]  # cap
    except Exception:
        return []


def _read_host_uptime_s() -> int:
    try:
        with open("/proc/uptime", "r") as f:
            return int(float(f.read().split()[0]))
    except Exception:
        return 0


def _read_disk_percent(path: str = "/") -> int:
    try:
        st = os.statvfs(path)
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        total = st.f_blocks * st.f_frsize
        if total <= 0:
            return 0
        return int(used * 100 / total)
    except Exception:
        return 0


def _read_mem_percent() -> int:
    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.read().splitlines()
        total = None
        avail = None
        for line in lines:
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
        if total and avail and total > 0:
            return int((total - avail) * 100 / total)
    except Exception:
        pass
    return 0


def build_runtime_info() -> dict[str, Any]:
    """Build the runtime snapshot posted with every heartbeat."""
    return {
        "node_id": SERVER_ID,
        "ts": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "docker_version": _read_docker_version(),
        "smsly_images": _read_smsly_images(),
        "host_uptime_s": _read_host_uptime_s(),
        "disk_used_pct": _read_disk_percent("/"),
        "mem_used_pct": _read_mem_percent(),
        "registrar_version": "1.0.0",
    }


# ── Active base URL discovery ─────────────────────────────────────────────
class BaseUrlResolver:
    """Pick the first base URL that actually answers a request.

    We try each URL in order and cache the first one that
    responds successfully. If it later fails, we fall through
    to the next URL. This way the registrar transparently
    follows the agent as it moves from public-IP bootstrap to
    WireGuard-mesh-only operation.
    """
    def __init__(self, urls: list[str]):
        self._urls = urls
        self._current: str | None = None

    def current(self) -> str | None:
        if not self._urls:
            return None
        if self._current is None:
            for url in self._urls:
                if self._probe(url):
                    self._current = url
                    log.info("discovered master at %s", url)
                    return self._current
            return None
        # Verify the current one is still reachable
        if self._probe(self._current, timeout=5):
            return self._current
        # Try the next one
        log.warning("master %s became unreachable, trying fallback", self._current)
        for url in self._urls:
            if url == self._current:
                continue
            if self._probe(url, timeout=5):
                self._current = url
                log.info("switched master to %s", url)
                return self._current
        self._current = None
        return None

    def _probe(self, url: str, timeout: int = 10) -> bool:
        try:
            req = urllib.request.Request(
                url.rstrip("/") + "/health/live",
                headers={"User-Agent": "smsly-agent-registrar/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status < 500
        except Exception:
            return False


# ── Sender with backoff ──────────────────────────────────────────────────
def post_with_backoff(resolver: BaseUrlResolver, path: str, body: dict) -> int:
    """Post JSON to the master with exponential backoff.

    Returns the HTTP status code (0 = transport failure). The
    call retries on any 0/5xx response but stops on a
    successful 2xx.
    """
    delay = 1.0
    last_status = 0
    last_error = ""
    for attempt in range(8):  # up to ~127s total
        base = resolver.current()
        if base is None:
            last_error = "no reachable master"
        else:
            last_status, last_error = _post_json(base, path, body)
            if 200 <= last_status < 300:
                return last_status
            # 4xx other than 401/408/429 are not worth retrying
            if 400 <= last_status < 500 and last_status not in {401, 403, 408, 429}:
                log.error(
                    "post %s -> HTTP %d: %s (non-retryable, giving up)",
                    path, last_status, last_error[:200],
                )
                return last_status
        log.warning(
            "post %s failed (attempt %d, status=%d): %s — retrying in %.0fs",
            path, attempt + 1, last_status, last_error[:200], delay,
        )
        time.sleep(delay)
        delay = min(delay * 2, 60.0)
    return last_status


# ── Shutdown handling ─────────────────────────────────────────────────────
_should_stop = False


def _on_signal(signum, frame):
    global _should_stop
    _should_stop = True
    log.info("received signal %d — shutting down gracefully", signum)


# ── Main loop ─────────────────────────────────────────────────────────────
_last_heartbeat_marker_path = "/tmp/registrar.last_heartbeat"


def _touch_heartbeat_marker():
    """Update a timestamp file so an external healthcheck can verify
    the registrar is alive. Touching on every iteration (even
    between sends) gives a finer-grained liveness signal than
    waiting for the next POST.
    """
    try:
        with open(_last_heartbeat_marker_path, "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def main() -> int:
    if not SERVER_ID:
        log.error("SERVER_ID is not set; cannot register with master")
        return 1
    if not GATEWAY_SECRET:
        log.error("GATEWAY_SECRET is not set; cannot register with master")
        return 1
    if not ALL_BASE_URLS:
        log.error("MASTER_API_URL is not set; cannot register with master")
        return 1

    log.info(
        "starting: server_id=%s, primary=%s, fallbacks=%d, heartbeat=%ds, register_on_start=%s",
        SERVER_ID, ALL_BASE_URLS[0], len(ALL_BASE_URLS) - 1,
        HEARTBEAT_INTERVAL, REGISTER_ON_START,
    )

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    resolver = BaseUrlResolver(ALL_BASE_URLS)

    if REGISTER_ON_START:
        body = {
            "status": "ok",
            "runtime_info": build_runtime_info(),
        }
        path = f"/api/v1/servers/{SERVER_ID}/agent-ready/"
        status = post_with_backoff(resolver, path, body)
        if 200 <= status < 300:
            log.info("✅ agent-ready reported successfully (HTTP %d)", status)
        else:
            log.warning(
                "agent-ready failed (HTTP %d) — will retry via heartbeats",
                status,
            )

    _touch_heartbeat_marker()
    next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
    while not _should_stop:
        now = time.monotonic()
        _touch_heartbeat_marker()
        if now >= next_heartbeat:
            body = {
                "status": "ok",
                "runtime_info": build_runtime_info(),
            }
            path = f"/api/v1/servers/{SERVER_ID}/agent-heartbeat/"
            status = post_with_backoff(resolver, path, body)
            if 200 <= status < 300:
                log.debug("heartbeat sent (HTTP %d)", status)
            else:
                log.warning("heartbeat failed (HTTP %d)", status)
            next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL

        # Sleep in small chunks so SIGTERM is responsive
        time.sleep(min(5.0, max(0.5, next_heartbeat - time.monotonic())))

    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
