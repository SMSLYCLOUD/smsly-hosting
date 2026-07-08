"""
Multi-server management views.

CRUD + health check + proxy + auto-provisioning for controlling
remote SMSLY Hosting instances.
"""

import hashlib
import hmac as hmac_mod
import ipaddress
import json as json_mod
import logging
import os
import re
import shlex
import ssl
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import requests
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.deployments.services.transfer_service import _redact_transfer_text

from .models_core import Deployment, Service
from .models_servers import ManagedServer
from .rate_limiting import ServerHealthCheckRateThrottle
from .serializers import DeploymentSerializer, ServiceSerializer

logger = logging.getLogger(__name__)

MANAGED_SERVER_HEALTH_TIMEOUT = 10


# ── Helpers for the agent-ready / agent-heartbeat endpoints ────────────────
#
# These two helpers are imported by the agent-ready/agent-heartbeat
# actions above. They're local to views_servers to avoid creating a new
# module for two short functions, but they encapsulate behaviour that
# would otherwise be inline.
def _truncate_dict(payload, max_items=20, max_str_len=120):
    """Sanitize an agent runtime snapshot before logging it.

    Trims to ``max_items`` keys and truncates string values to
    ``max_str_len`` chars so a verbose Docker stats payload doesn't
    blow up the provision log.
    """
    if not isinstance(payload, dict):
        return {}
    out = {}
    for idx, (k, v) in enumerate(payload.items()):
        if idx >= max_items:
            break
        v = str(v)
        if len(v) > max_str_len:
            v = v[:max_str_len] + "..."
        out[str(k)[:64]] = v
    return out


def _append_log_safe(server, message):
    """Best-effort log append. Never raises — agents rely on this."""
    try:
        from .services.provisioner import _append_log
        _append_log(server, message)
    except Exception:
        # If provisioner imports cycle, fall back to writing directly
        # to the model. Still best-effort.
        try:
            from django.utils import timezone as _tz
            line = f"[{_tz.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
            server.provision_logs = (server.provision_logs or "") + line + "\n"
            server.save(update_fields=["provision_logs", "updated_at"])
        except Exception:
            pass


def _append_unique(values: list[str], value: str):
    normalized = str(value or "").strip().rstrip("/")
    if normalized and normalized not in values:
        values.append(normalized)


def _server_host_port(server) -> str:
    value = str(server.host or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.netloc or parsed.path
    return value.split("/")[0].strip()


def _server_host_is_ip(host_port: str) -> bool:
    host = host_port.rsplit(":", 1)[0] if host_port.count(":") == 1 else host_port
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _candidate_api_urls(server) -> list[str]:
    """Return likely API base URLs in the order we should probe.

    Priority:
      1. WireGuard mesh VPN IP (secure, internal, encrypted)
      2. Public IP / Domain (fallback)
    """
    urls: list[str] = []
    current = str(server.api_url or "").strip()
    _append_unique(urls, current)

    host_port = _server_host_port(server)
    wg_ip = str(getattr(server, "wg_address", "") or "").strip()
    has_wg = bool(wg_ip and wg_ip != host_port)
    is_lite = getattr(server, 'is_lite_agent', False)

    # ── Priority 1: WireGuard Mesh VPN (secure, internal, encrypted) ──
    if has_wg:
        if is_lite:
            # Lite agent: backend listens on :8000 on its own host.
            # Both the bare IP and an explicit :8090 are tried because
            # the master→agent traffic inside the VPN is direct, and
            # some lite deployments run a Traefik on :8090 in front of
            # the backend. The bare :8000 form is the agent's own
            # gunicorn.
            _append_unique(urls, f"http://{wg_ip}:8000")
            _append_unique(urls, f"http://{wg_ip}:8090")
            _append_unique(urls, f"http://{wg_ip}")
        else:
            _append_unique(urls, f"http://{wg_ip}:8090")
            _append_unique(urls, f"http://{wg_ip}")

    if not host_port:
        return urls

    has_explicit_port = host_port.count(":") == 1

    # ── Priority 2: Public IP / Domain (fallback) ──
    if _server_host_is_ip(host_port):
        if is_lite:
            # Same priority order as above for the public IP path.
            _append_unique(urls, f"http://{host_port}:8000")
            _append_unique(urls, f"http://{host_port}:8090")
            _append_unique(urls, f"http://{host_port}")
        else:
            _append_unique(urls, f"http://{host_port}:8090")
            _append_unique(urls, f"http://{host_port}")
        _append_unique(urls, f"https://{host_port}")

        if host_port.startswith("127.0.0.1") or host_port.startswith("localhost"):
            _append_unique(urls, f"http://{host_port}:8000")
    else:
        _append_unique(urls, f"https://{host_port}")
        _append_unique(urls, f"http://{host_port}")
        if not has_explicit_port:
            _append_unique(urls, f"http://{host_port}:8090")

        if host_port.startswith("localhost"):
            _append_unique(urls, f"http://{host_port}:8000")

    return urls


def _extract_health_version(response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    version = payload.get("version")
    return str(version).strip() if version else ""


def _detect_reachable_api_url(server) -> tuple[str | None, Any | None]:
    """Probe candidate base URLs and return the first one that responds."""
    # Try multiple health paths: /health is the standard endpoint,
    # /health/live is the liveness-only probe used by some lite agent configs.
    health_paths = ("/health", "/health/live")
    from .services.tls_verify import _check_pin_after_handshake, resolve_tls_verify
    verify, fingerprint = resolve_tls_verify(server)
    for base_url in _candidate_api_urls(server):
        for health_path in health_paths:
            try:
                # SECURITY: TLS verification is now per-server
                # (ManagedServer.verify_tls, default True) and can
                # be tightened with a SHA-256 cert pin
                # (ManagedServer.tls_cert_sha256). The legacy
                # hard-coded ``verify=False`` was removed in
                # Batch G to prevent a MITM on the inter-node
                # connection from capturing the gateway_secret.
                response = requests.get(
                    f"{base_url}{health_path}",
                    timeout=MANAGED_SERVER_HEALTH_TIMEOUT,
                    verify=verify,
                    stream=True,
                )
                if fingerprint:
                    _check_pin_after_handshake(response, fingerprint)
                
                # Consume and close so connection returns to pool
                _ = response.content
                response.close()

            except (requests.RequestException, ssl.SSLError):
                continue

            # If it's 5xx, it's a server error but the server IS reachable.
            # However, we only mark as 'ONLINE' if it returns a non-5xx code
            # to ensure the management layer is actually healthy.
            if response.status_code < 500:
                return base_url, response

    return None, None


def _refresh_managed_server_health(server):
    """
    Detect a reachable API URL, update server health fields, and sync service count.
    """
    base, health_response = _detect_reachable_api_url(server)
    update_fields = {"status", "last_health_check", "services_count"}

    if base:
        if server.api_url != base:
            server.api_url = base
            update_fields.add("api_url")

        server.status = ManagedServer.Status.ONLINE

        version = _extract_health_version(health_response)
        if version and server.server_version != version:
            server.server_version = version
            update_fields.add("server_version")

        # Lite agents share the master DB — count services locally
        if getattr(server, 'is_lite_agent', False):
            server.services_count = Service.objects.filter(server=server).count()
        else:
            api_path = "/api/v1/services/"
            headers = _build_remote_headers(server, method="GET", path=api_path)
            try:
                resp = requests.get(f"{base}{api_path}", headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    services = data.get("results", data) if isinstance(data, dict) else data
                    server.services_count = len(services) if isinstance(services, list) else 0
            except requests.RequestException:
                pass
    else:
        server.status = ManagedServer.Status.OFFLINE

    server.last_health_check = timezone.now()
    server.save(update_fields=list(update_fields))

    if server.status == ManagedServer.Status.ONLINE:
        has_token = bool(str(server.api_token or "").strip())
        if not has_token or server.services_count == 0:
            new_token = _try_auto_token_exchange(server, server.api_url.rstrip("/"))
            if new_token:
                server.api_token = new_token
                server.save(update_fields=["api_token", "updated_at"])

                api_path = "/api/v1/services/"
                headers = _build_remote_headers(server, method="GET", path=api_path)
                try:
                    resp = requests.get(
                        f"{server.api_url.rstrip('/')}{api_path}",
                        headers=headers,
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        services = data.get("results", data) if isinstance(data, dict) else data
                        server.services_count = len(services) if isinstance(services, list) else 0
                        server.save(update_fields=["services_count"])
                except requests.RequestException:
                    pass

        try:
            if not server.is_primary and (server.ssh_key or server.ssh_password):
                from apps.deployments.services.wireguard_service import WireGuardService
                WireGuardService.ensure_server_in_default_mesh(
                    server,
                    deploy_async=True,
                )
        except Exception as exc:
            logger.warning("Automatic VPN mesh setup failed for %s: %s", server.id, exc)

    return server


def _try_auto_token_exchange(server, base_url: str) -> str | None:
    """
    Attempt to obtain an API token from a remote server automatically.

    Strategies (in order):
    1. HMAC exchange: If gateway_secret is set, use it to request a token.
    2. Credential exchange: If SSH password is available, try admin login.

    Tries each candidate API URL for the server in turn.
    Returns the raw token string on success, None on failure.
    """
    gateway_secret = str(server.gateway_secret or "").strip()
    ssh_password = str(server.ssh_password or "").strip()
    candidate_urls = _candidate_api_urls(server)

    def _try_exchange_for_url(url_base: str) -> str | None:
        url_base = str(url_base or "").strip().rstrip("/")
        if not url_base:
            return None

        # ── Strategy 1: HMAC-based exchange ──
        if gateway_secret:
            try:
                ts = str(int(time.time()))
                body = json_mod.dumps({"node_name": f"Node-{server.host}"}, sort_keys=True).encode()
                body_hash = hashlib.sha256(body).hexdigest()
                path = "/api/v1/auth/node-token-exchange-hmac/"
                payload = f"POST|{path}|{ts}|{body_hash}"
                sig = hmac_mod.new(gateway_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

                from .services.tls_verify import (
                    _check_pin_after_handshake,
                    resolve_tls_verify,
                )
                verify, fingerprint = resolve_tls_verify(server)
                resp = requests.post(
                    f"{url_base}{path}",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Gateway-Signature-V2": sig,
                        "X-Request-Timestamp": ts,
                    },
                    timeout=15,
                    verify=verify,
                    stream=True,
                )
                if fingerprint:
                    _check_pin_after_handshake(resp, fingerprint)
                _ = resp.content
                resp.close()
                if resp.status_code == 200:
                    token = resp.json().get("token")
                    if token:
                        logger.info("Auto-exchanged HMAC for API token on %s via %s", server.host, url_base)
                        return token
            except Exception as exc:
                logger.debug("HMAC token exchange failed for %s via %s: %s", server.host, url_base, exc)

        allow_pw_exchange = str(os.environ.get("ALLOW REMOTE_PASSWORD_EXCHANGE", "")).lower() in {
            "1", "true", "yes", "on"
        }

        # ── Strategy 2: Credential-based exchange ──
        if ssh_password and allow_pw_exchange:
            for username in ("admin", "root"):
                try:
                    resp = requests.post(
                        f"{url_base}/api/v1/auth/node-token-exchange/",
                        json={
                            "username": username,
                            "password": ssh_password,
                            "node_name": f"Node-{server.host}",
                        },
                        timeout=15,
                        verify=verify,
                        stream=True,
                    )
                    if fingerprint:
                        _check_pin_after_handshake(resp, fingerprint)
                    _ = resp.content
                    resp.close()
                    if resp.status_code == 200:
                        token = resp.json().get("token")
                        if token:
                            logger.info(
                                "Auto-exchanged credentials (%s) for API token on %s via %s",
                                username, server.host, url_base,
                            )
                            return token
                except Exception as exc:
                    logger.debug(
                        "Credential token exchange (%s) failed for %s via %s: %s",
                        username, server.host, url_base, exc,
                    )

        # ── Strategy 3: Login API (dj-rest-auth) ──
        if ssh_password and allow_pw_exchange:
            for username in ("admin", "root"):
                try:
                    resp = requests.post(
                        f"{url_base}/api/v1/auth/login/",
                        json={"username": username, "password": ssh_password},
                        timeout=15,
                        verify=verify,
                        stream=True,
                    )
                    if fingerprint:
                        _check_pin_after_handshake(resp, fingerprint)
                    _ = resp.content
                    resp.close()
                    if resp.status_code == 200:
                        token = resp.json().get("key") or resp.json().get("token")
                        if token:
                            logger.info(
                                "Auto-obtained DRF token via login for %s via %s",
                                server.host, url_base,
                            )
                            return token
                except Exception:
                    pass

        return None

    # Try each candidate URL in order, returning on first success
    for url_base in candidate_urls:
        token = _try_exchange_for_url(url_base)
        if token:
            return token

    return None


def _safe_remote_error_payload(kind: str, reason: str, upstream_status: int | None = None) -> dict[str, Any]:
    """
    Return a non-failing payload for remote convenience endpoints.

    This keeps dashboard pages usable even when a remote server is
    temporarily unreachable or still provisioning.
    """
    payload: dict[str, Any] = {
        "remote_unreachable": True,
        "remote_error": str(reason),
        "kind": kind,
    }
    if upstream_status is not None:
        payload["upstream_status"] = int(upstream_status)

    if kind in {"services", "deployments"}:
        payload["results"] = []
        payload["count"] = 0
    elif kind == "domains":
        payload["domains"] = []
        payload["count"] = 0

    return payload


def _proxy_error_response(reason: str, upstream_status: int = 502) -> Response:
    """Return remote proxy failures in the normal proxy envelope."""
    return Response({
        "status_code": upstream_status,
        "data": {
            "remote_unreachable": True,
            "error": str(reason),
            "upstream_status": upstream_status,
        },
    })


def _build_remote_headers(server, method="GET", path="/api/v1/services/", body=b"", auth_mode=None):
    """
    Build auth headers for a remote server.
    Strategy: token auth when available, otherwise HMAC V2 signing.
    """
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-SMSLY-Remote-Sync": "1",
    }

    token = str(server.api_token or "").strip()
    gateway_secret = str(server.gateway_secret or "").strip()

    def _apply_token_auth():
        if not token:
            return False
        if token.lower().startswith("token ") or token.lower().startswith("bearer "):
            headers["Authorization"] = token
        elif token.startswith("smsly_"):
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers["Authorization"] = f"Token {token}"
        return True

    def _apply_hmac_auth():
        if not gateway_secret:
            return False
        import secrets as _secrets
        timestamp = str(int(time.time()))
        nonce = _secrets.token_urlsafe(16)
        body_hash = hashlib.sha256(body if isinstance(body, bytes) else b"").hexdigest()
        # SECURITY (Batch G): canonical payload format
        # {method}|{path}|{timestamp}|{nonce}|{body_hash}.
        # Matches ZeroTrustHMACAuthentication on the server side.
        payload = f"{method}|{path}|{timestamp}|{nonce}|{body_hash}"
        signature = hmac_mod.new(
            gateway_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Gateway-Signature-V2"] = signature
        headers["X-Request-Timestamp"] = timestamp
        headers["X-Request-Nonce"] = nonce
        return True

    # Explicit mode for fallback paths.
    if auth_mode == "token":
        _apply_token_auth()
        return headers
    if auth_mode == "hmac":
        _apply_hmac_auth()
        return headers

    # Default mode.
    if _apply_token_auth():
        return headers
    if _apply_hmac_auth():
        return headers

    # No auth available: try anyway (will likely fail)
    return headers


def _iter_remote_auth_modes(server):
    """Return auth modes in fallback order for remote requests."""
    modes = []
    if str(server.api_token or "").strip():
        modes.append("token")
    if str(server.gateway_secret or "").strip():
        modes.append("hmac")
    if not modes:
        modes.append("none")
    return modes


def _normalize_remote_api_path(path_or_url):
    """Convert relative or absolute next links into an API path."""
    value = str(path_or_url or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path
    if not value.startswith("/"):
        return f"/{value}"
    return value


def _extract_page_results_and_next(payload):
    """Extract list results and pagination next pointer from API payload."""
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        results = payload.get("results", [])
        if not isinstance(results, list):
            results = []
        return results, payload.get("next")
    return [], None


def _fetch_remote_json_with_fallback(server, kind, api_path, timeout=15):
    """
    Fetch remote JSON with token -> HMAC fallback.

    Tries each candidate API URL for the server in turn,
    with multiple auth modes per URL.
    """
    normalized_path = _normalize_remote_api_path(api_path)
    candidate_urls = _candidate_api_urls(server)
    modes = _iter_remote_auth_modes(server)
    retryable_statuses = {401, 403, 500, 502, 503}

    last_status = None

    for base_url in candidate_urls:
        url = f"{str(base_url or '').rstrip('/')}{normalized_path}"
        for idx, mode in enumerate(modes):
            headers = _build_remote_headers(
                server,
                method="GET",
                path=normalized_path,
                auth_mode=None if mode == "none" else mode,
            )
            try:
                resp = requests.get(url, headers=headers, timeout=timeout)
            except requests.RequestException as exc:
                str(exc)
                continue

            last_status = resp.status_code
            if resp.status_code >= 400:
                has_more_modes = idx < len(modes) - 1
                if has_more_modes and resp.status_code in retryable_statuses:
                    continue
                # Don't fail-fast on non-retryable status; try next candidate URL
                break

            try:
                return resp.json(), None
            except ValueError:
                has_more_modes = idx < len(modes) - 1
                if has_more_modes:
                    continue
                break

    return None, _safe_remote_error_payload(
        kind,
        f"Remote server unreachable or returned HTTP {last_status}" if last_status else "Remote server unreachable",
        upstream_status=last_status,
    )


def _lite_agent_proxy_response(server, request, method: str, path: str) -> Response | None:
    """Serve safe lite-agent proxy reads from the shared controller database."""
    if not getattr(server, "is_lite_agent", False) or method != "GET":
        return None

    path_only = path.split("?", 1)[0].rstrip("/")
    if path_only == "/api/v1/services":
        services_qs = (
            Service.objects
            .filter(server=server)
            .exclude(status=Service.Status.DELETED)
            .select_related("project")
            .order_by("-updated_at")
        )
        data = ServiceSerializer(services_qs, many=True, context={"request": request}).data
        return Response({"status_code": 200, "data": {"results": data, "count": len(data)}})

    if path_only == "/api/v1/deployments":
        deployments_qs = (
            Deployment.objects
            .filter(service__server=server)
            .select_related("service")
            .order_by("-created_at")[:50]
        )
        data = DeploymentSerializer(deployments_qs, many=True).data
        return Response({"status_code": 200, "data": {"results": data, "count": len(data)}})

    service_prefix = "/api/v1/services/"
    if path_only.startswith(service_prefix):
        raw_id = path_only[len(service_prefix):].split("/", 1)[0]
        try:
            service_id = uuid.UUID(raw_id)
        except (TypeError, ValueError):
            return None
        service = (
            Service.objects
            .filter(id=service_id, server=server)
            .exclude(status=Service.Status.DELETED)
            .select_related("project")
            .first()
        )
        if service:
            data = ServiceSerializer(service, context={"request": request}).data
            return Response({"status_code": 200, "data": data})

    return None


SAFE_DOCKER_SUBCOMMANDS = frozenset({
    "ps", "logs", "stats", "inspect", "images", "info", "version",
    "df", "top", "port", "events", "system",
})
SAFE_DOCKER_COMPOSE_SUBCOMMANDS = frozenset({
    "ps", "logs", "config", "ls", "top",
})
SAFE_SYSTEM_PREFIXES = (
    "df ", "free ", "ping -c ", "systemctl status ",
    "cat /opt/smsly/*.log",
)

_REJECTED_DOCKER_SUBCOMMANDS = frozenset({
    "exec", "run", "rm", "kill", "rmi", "stop", "restart",
    "pause", "unpause", "rename", "update", "wait", "attach",
    "commit", "cp", "create", "diff", "export", "import",
    "load", "save", "tag", "unmount", "build", "pull", "push",
    "login", "logout", "search", "volume", "network", "plugin",
    "secret", "config", "context", "node", "service", "stack",
    "swarm", "system", "trust", "container", "compose", "daemon",
})

_FORBIDDEN_SHELL_METACHARS = re.compile(r'[;|&`$()<>\n\r\\]|\$\(')
_SAFE_COMMANDS = (
    'docker', 'docker-compose', 'systemctl', 'journalctl',
    'ls', 'cat', 'head', 'tail', 'grep', 'awk', 'sed',
    'ps', 'top', 'free', 'df', 'du', 'uptime',
    'ping', 'traceroute', 'ss', 'netstat', 'ip', 'ifconfig',
)


FORBIDDEN_PATH_PATTERNS = (
    ".env",
    "/etc/shadow",
    "/etc/passwd",
    "/etc/sudoers",
)


_DOCKER_DENIED_SUBCOMMANDS = frozenset({
    'compose', 'exec', 'run', 'swarm', 'system', 'trust',
    'container', 'daemon', 'stack', 'service', 'node',
    'secret', 'config', 'network', 'volume', 'plugin',
    'image', 'buildx', 'context', 'manifest',
    'checkpoint', 'attach', 'wait', 'kill',
    'pause', 'unpause', 'rename', 'restart', 'start',
    'stop', 'update', 'rm', 'rmi',
})


def _is_command_allowed(command: str) -> bool:
    """Strict allow-list for run_command. Returns True if the command is permitted."""
    if not command or not isinstance(command, str):
        return False
    if _FORBIDDEN_SHELL_METACHARS.search(command):
        return False
    if '`' in command or '$(' in command:
        return False
    stripped = command.strip()
    if not stripped:
        return False
    try:
        parts = shlex.split(stripped)
    except ValueError:
        return False
    if not parts:
        return False
    if parts[0] not in _SAFE_COMMANDS:
        return False
    if parts[0] in ('docker', 'docker-compose') and len(parts) > 1 and parts[1] in _DOCKER_DENIED_SUBCOMMANDS:
        return False
    for arg in parts:
        for bad in FORBIDDEN_PATH_PATTERNS:
            if bad in arg:
                return False
    # SEC (Issue 75): ``docker logs <name>`` lets a user read the logs of
    # any container running on the host, including other tenants'.
    # Restrict it to containers the calling user actually owns. The
    # ``Service`` row is the authoritative owner record — its ``name``
    # matches the docker container name.  We cross-check both
    # ``Service.name == args[2]`` (the target the user wants to tail)
    # and ``Service.owner == request.user`` so a tenant cannot pass
    # another tenant's container name.
    if (
        len(parts) >= 3
        and parts[0] == 'docker'
        and parts[1] == 'logs'
    ):
        target_name = parts[2]
        if target_name.startswith('-'):
            return False
        user = _current_request_user()
        if user is None:
            return False
        if not _user_owns_container_name(user, target_name):
            return False
    return True


_DOCKER_LOGS_OWNER_CACHE: dict[str, bool] = {}


def _user_owns_container_name(user, name: str) -> bool:
    """Return True when the given container ``name`` is owned by ``user``.

    Cross-checks ``Service.name == name`` (the docker container name
    the caller wants to tail) and ``Service.owner == user`` (the
    owner claim). Cached per-name for the lifetime of the worker
    process to avoid hitting the ORM on every ``docker logs``
    invocation.
    """
    cache_key = f"{getattr(user, 'id', None)}:{name}"
    if cache_key in _DOCKER_LOGS_OWNER_CACHE:
        return _DOCKER_LOGS_OWNER_CACHE[cache_key]
    try:
        owned = Service.objects.filter(name=name, owner=user).exists()
    except Exception:
        owned = False
    _DOCKER_LOGS_OWNER_CACHE[cache_key] = owned
    return owned


def _current_request_user():
    """Best-effort lookup of the user attached to the current request."""
    from threading import current_thread

    thread = current_thread()
    return getattr(thread, 'smsly_request_user', None)


def _bind_request_user(user):
    """Bind the current user to the worker thread for ``_is_command_allowed``."""
    from threading import current_thread

    current_thread().smsly_request_user = user


# --- Serializers -------------------------------------------------------------
class ManagedServerSerializer(serializers.ModelSerializer):
    has_ssh_credentials = serializers.SerializerMethodField()
    # SECURITY (Batch G cont): whether a TLS cert SHA-256 pin
    # is configured. We never return the pin itself — only a
    # boolean — so the serializer is safe to surface in the
    # read API. The pin is set via a separate admin action.
    tls_cert_sha256_set = serializers.SerializerMethodField()

    def get_has_ssh_credentials(self, obj):
        return bool(str(obj.ssh_password or '').strip() or str(obj.ssh_key or '').strip())

    class Meta:
        model = ManagedServer
        fields = [
            "id", "name", "host", "private_ip", "api_url", "ssh_port",
            "ssh_user", "provider_metadata", "is_primary",
            "allow_user_workloads", "status", "last_health_check",
            "server_version", "services_count", "created_at",
            "provision_status", "provision_logs", "role", "wg_address",
            "has_ssh_credentials", "is_lite_agent",
            # Agent self-registration signals: surfaced so operators
            # can tell at a glance whether the agent's installer
            # has finished bootstrapping and how recently the
            # registrar last reported in. See models_core.py for
            # the field-level rationale.
            "agent_ready", "last_agent_heartbeat_at", "agent_runtime_info",
            # SECURITY (Batch G cont): expose the per-server TLS
            # verification settings so operators can audit which
            # nodes run with verify_tls=False. tls_cert_sha256 is
            # write-only (the pin is never echoed back to API
            # consumers); tls_cert_sha256_set is a boolean indicator
            # so operators can see whether a pin is configured
            # without leaking the pin value itself.
            "verify_tls", "tls_cert_sha256_set",
        ]
        read_only_fields = [
            "id", "status", "last_health_check", "server_version",
            "services_count", "created_at", "provision_status",
            "role", "wg_address", "has_ssh_credentials", "is_lite_agent",
            "agent_ready", "last_agent_heartbeat_at", "agent_runtime_info",
            "tls_cert_sha256_set",
        ]

    def get_tls_cert_sha256_set(self, obj):
        """Return whether a TLS cert SHA-256 pin is configured, without
        revealing the pin itself."""
        return bool((getattr(obj, "tls_cert_sha256", "") or "").strip())


class ManagedServerCreateSerializer(serializers.ModelSerializer):
    """For 'Connect Existing' mode — user provides api_url + api_token."""
    node_certificate = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )

    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "private_ip", "api_url", "api_token",
            "gateway_secret", "ssh_user", "ssh_password", "ssh_key",
            "ssh_port", "is_primary", "allow_user_workloads",
            "provider_metadata", "is_lite_agent", "node_certificate",
        ]
        extra_kwargs = {
            "api_token": {"write_only": True, "required": False},
            "gateway_secret": {"write_only": True, "required": False},
            "ssh_key": {"write_only": True, "required": False, "trim_whitespace": False},
            "ssh_password": {"write_only": True, "required": False},
            "provider_metadata": {"required": False},
        }

    def validate(self, data):
        is_lite = data.get("is_lite_agent", False)
        if is_lite:
            cert = (data.get("node_certificate") or "").strip()
            if not cert:
                raise serializers.ValidationError(
                    {"node_certificate": "node_certificate is required when is_lite_agent=True."}
                )
        return data

    def create(self, validated_data):
        cert = validated_data.pop("node_certificate", None)
        if cert and cert.strip():
            import hashlib
            validated_data["tls_cert_sha256"] = hashlib.sha256(cert.strip().encode('utf-8')).hexdigest()
        return super().create(validated_data)

    def update(self, instance, validated_data):
        cert = validated_data.pop("node_certificate", None)
        if cert and cert.strip():
            import hashlib
            validated_data["tls_cert_sha256"] = hashlib.sha256(cert.strip().encode('utf-8')).hexdigest()
        return super().update(instance, validated_data)

    def validate_ssh_key(self, value):
        if value and value.strip():
            key = value.strip()
            if not key.startswith('-----BEGIN '):
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Must be a valid PEM-encoded private key "
                    "starting with '-----BEGIN ... PRIVATE KEY-----'."
                )
            if '-----END ' not in key:
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Missing '-----END ... PRIVATE KEY-----' footer."
                )
        return value

    def to_representation(self, instance):
        # Return the stable read serializer shape after create/update operations.
        return ManagedServerSerializer(instance).data

    def validate_host(self, value):
        """Strip protocol and port, then enforce safe-IP policy for non-primary servers."""
        import re
        value = re.sub(r'^https?://', '', (value or "")).strip().rstrip('/')
        value = re.sub(r':\d+$', '', value)
        if not value:
            raise serializers.ValidationError("Host is required.")
        is_primary = self.initial_data.get("is_primary", False)
        if not is_primary:
            try:
                import ipaddress as _ip
                ip = _ip.ip_address(value)
                if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
                    raise serializers.ValidationError(
                        f"Host {value} is in a forbidden range for non-primary servers."
                    )
            except ValueError:
                pass  # Hostname — allowed for non-primary
        if value.lower() == "localhost" and not is_primary:
            raise serializers.ValidationError("'localhost' is not allowed as a non-primary host.")
        return value

    def validate_api_url(self, value):
        """Ensure api_url has a protocol prefix. Default to HTTP for IPs.

        SECURITY (Batch G): reject api_url that points at any
        non-public address (RFC1918, link-local, loopback,
        multicast, reserved, unspecified). These are SSRF targets
        that the operator (not the user) should be able to reach.
        A user that can register a server with api_url pointing at
        the platform's own controller would otherwise be able to
        relay requests to the controller's admin endpoints via
        the ``/proxy/`` action.
        """
        import ipaddress
        from urllib.parse import urlparse
        value = (value or "").strip().rstrip('/')
        if value and not value.startswith(('http://', 'https://')):
            host_part = value.split(':')[0]
            try:
                ipaddress.ip_address(host_part)
                value = f'http://{value}'
            except ValueError:
                value = f'https://{value}'
        if value:
            parsed = urlparse(value)
            hostname = (parsed.hostname or '').lower()
            if hostname in ('localhost',) or hostname.endswith('.localhost'):
                raise serializers.ValidationError(
                    f"api_url hostname {hostname!r} is a loopback / internal target."
                )
            try:
                ip = ipaddress.ip_address(hostname)
                # SECURITY: reject ALL non-global unicast addresses.
                # A valid user-registered node has a public IP
                # (the operator's VPS), so anything in private
                # ranges is an SSRF target.
                from django.conf import settings
                allow_local = getattr(settings, 'ALLOW_LOCAL_NODES', False)
                if not allow_local:
                    if not ip.is_global or (
                        ip.is_loopback or ip.is_link_local
                        or ip.is_multicast or ip.is_reserved
                        or ip.is_unspecified or ip.is_private
                    ):
                        raise serializers.ValidationError(
                            f"api_url IP {ip} is not a public address "
                            f"(loopback / private / link-local / reserved)."
                        )
            except ValueError:
                pass  # hostname — allowed
        return value


class ManagedServerProvisionSerializer(serializers.ModelSerializer):
    """For 'Provision New' mode — user provides SSH credentials."""
    ssh_auth_method = serializers.ChoiceField(
        choices=["password", "key"], write_only=True, required=False, default="password"
    )
    node_certificate = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
    )

    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "ssh_port", "ssh_user",
            "ssh_password", "ssh_key", "ssh_auth_method",
            "is_primary", "allow_user_workloads", "is_lite_agent",
            "node_certificate",
        ]
        extra_kwargs = {
            "ssh_password": {"write_only": True},
            "ssh_key": {"write_only": True, "trim_whitespace": False},
        }

    def validate_ssh_key(self, value):
        if value and value.strip():
            key = value.strip()
            if not key.startswith('-----BEGIN '):
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Must be a valid PEM-encoded private key "
                    "starting with '-----BEGIN ... PRIVATE KEY-----'."
                )
            if '-----END ' not in key:
                raise serializers.ValidationError(
                    "Invalid SSH private key format. Missing '-----END ... PRIVATE KEY-----' footer."
                )
        return value

    def validate(self, data):
        method = data.get("ssh_auth_method", "password")
        if method == "password" and not data.get("ssh_password"):
            raise serializers.ValidationError(
                {"ssh_password": "Password is required for password auth."}
            )
        if method == "key" and not data.get("ssh_key"):
            raise serializers.ValidationError(
                {"ssh_key": "SSH private key is required for key auth."}
            )
        # If provisioning via SSH, we don't require the certificate upfront.
        # The provisioner script will automatically fetch it from the remote node
        # once the lite agent is installed.
        return data


# ─── ViewSet ─────────────────────────────────────────────────────────────────

class ServerCommandThrottle(UserRateThrottle):
    scope = 'server_run_command'


class ServerHealThrottle(UserRateThrottle):
    scope = 'server_heal'


class ServerProxyThrottle(UserRateThrottle):
    scope = 'server_proxy'


ALLOWED_PROXY_METHODS = {'GET', 'HEAD'}
ALLOWED_PROXY_PATHS = (
    '/api/v1/health',
    '/api/v1/metrics',
)


class ServerCheckAllThrottle(UserRateThrottle):
    scope = 'server_check_all'


class ServerProvisionThrottle(UserRateThrottle):
    scope = 'server_provision'


class ManagedServerViewSet(viewsets.ModelViewSet):
    """CRUD for managed remote servers, plus health check, proxy, and provisioning."""

    queryset = ManagedServer.objects.all()
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        """Per-action throttles: prefer the bound action's ``throttle_classes``
        when present, otherwise fall back to the view's class-level setting.
        """
        action_attr = getattr(self, self.action, None) if self.action else None
        action_throttles = getattr(action_attr, "throttle_classes", None)
        if action_throttles:
            return [throttle() for throttle in action_throttles]
        return super().get_throttles()

    def get_queryset(self):
        qs = self.queryset.filter(owner=self.request.user)
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def get_serializer_class(self):
        if self.action == "provision_new":
            return ManagedServerProvisionSerializer
        if self.action in ["create", "update", "partial_update"]:
            return ManagedServerCreateSerializer
        return ManagedServerSerializer

    def perform_create(self, serializer):
        server = serializer.save(owner=self.request.user)
        self._start_server_health_sync(server)

    def perform_update(self, serializer):
        server = serializer.save()
        self._start_server_health_sync(server)

    def _start_server_health_sync(self, server):
        """Start background health/auth/mesh repair for a connected server."""
        has_connection_hint = bool(
            server.api_url
            or server.api_token
            or server.gateway_secret
            or server.ssh_key
            or server.ssh_password
        )
        if server.host and has_connection_hint:
            from threading import Thread
            Thread(target=self._sync_server_health, args=(server.id,), daemon=True).start()

    def _sync_server_health(self, server_id):
        """Background worker to check a server's health and service count upon connection."""
        try:
            server = ManagedServer.objects.get(id=server_id)
            _refresh_managed_server_health(server)
        except Exception as e:
            logger.warning(f"Background server sync failed for {server_id}: {e}")

    # ── Provision New Server ─────────────────────────────────────────────

    @action(detail=False, methods=["post"], url_path="provision")
    @throttle_classes([ServerProvisionThrottle])
    def provision_new(self, request):
        """
        Create a server record and kick off auto-provisioning via SSH.
        The installer will run on the remote VPS and auto-fill api_url/api_token.

        SECURITY: marking a server as ``is_primary=True`` displaces the
        platform's existing primary control plane. Any non-superuser
        with valid SSH credentials could previously register a
        competing primary and seize traffic for their workloads.
        Only superusers may set the flag.
        """
        is_primary_raw = request.data.get("is_primary", False)
        if isinstance(is_primary_raw, str):
            is_primary_requested = is_primary_raw.strip().lower() in (
                "true", "1", "yes", "t", "on",
            )
        else:
            is_primary_requested = bool(is_primary_raw)
        if is_primary_requested and not request.user.is_superuser:
            return Response(
                {"error": "Only superusers can provision a primary server."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ManagedServerProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Remove the non-model field before saving
        validated = serializer.validated_data.copy()
        validated.pop("ssh_auth_method", None)
        validated.pop("node_certificate", None)

        server = ManagedServer.objects.create(
            owner=request.user,
            provision_status=ManagedServer.ProvisionStatus.PENDING,
            **validated,
        )
        if server.is_primary and server.allow_user_workloads:
            server.allow_user_workloads = False
            server.save(update_fields=["allow_user_workloads", "updated_at"])

        # Kick off async provisioning
        from .services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["post"], url_path="provision-batch")
    @throttle_classes([ServerProvisionThrottle])
    def provision_batch(self, request):
        """Provision multiple lite agent servers in parallel.

        Accepts {"servers": [...]} where each item has the same fields as
        the single provision endpoint (name, host, ssh_password, etc.).

        Returns 202 with a list of created server records. Each server's
        provisioning runs as an independent Celery task.
        """
        servers_data = request.data.get("servers")
        if not servers_data or not isinstance(servers_data, list):
            return Response(
                {"error": "'servers' must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(servers_data) > 20:
            return Response(
                {"error": "Maximum 20 servers per batch."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        errors = []
        from .services.provisioner import provision_server

        for idx, item in enumerate(servers_data):
            serializer = ManagedServerProvisionSerializer(data=item)
            if not serializer.is_valid():
                errors.append({"index": idx, "host": item.get("host", ""), "errors": serializer.errors})
                continue

            validated = serializer.validated_data.copy()
            validated.pop("ssh_auth_method", None)
            validated.pop("node_certificate", None)

            # Force lite agent for batch provisioning
            validated["is_lite_agent"] = True
            validated["is_primary"] = False

            server = ManagedServer.objects.create(
                owner=request.user,
                provision_status=ManagedServer.ProvisionStatus.PENDING,
                **validated,
            )
            provision_server.delay(str(server.id))
            created.append(ManagedServerSerializer(server).data)

        return Response(
            {"created": created, "errors": errors, "total": len(created)},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"], url_path="provision-logs")
    def provision_logs(self, request, pk=None):
        """Get the provisioning logs for a server."""
        server = self.get_object()
        return Response({
            "provision_status": server.provision_status,
            "provision_logs": server.provision_logs,
        })

    @action(detail=True, methods=["post"], url_path="retry-provision")
    @throttle_classes([ServerProvisionThrottle])
    def retry_provision(self, request, pk=None):
        """Retry the provisioning process for an existing server."""
        server = self.get_object()

        # Reset status and clear logs
        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Retry started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        # Kick off async provisioning
        from .services.provisioner import provision_server
        provision_server.delay(str(server.id))

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="update-server")
    @throttle_classes([ServerProvisionThrottle])
    def update_server(self, request, pk=None):
        """
        Trigger a remote update on a managed server.

        Uses the same idempotent provision flow (install.sh) which handles
        both fresh installs and updates without clearing existing data/volumes.
        """
        server = self.get_object()

        blocked_statuses = {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
            ManagedServer.ProvisionStatus.UPDATING,
        }
        if server.provision_status in blocked_statuses:
            logger.warning(
                "update_server: auto-clearing in-flight provision_status=%s for server %s (user=%s)",
                server.provision_status, server.id, request.user.id,
            )
            server.provision_status = ManagedServer.ProvisionStatus.DONE
            server.save(update_fields=["provision_status", "updated_at"])

        if not (server.ssh_key or server.ssh_password):
             return Response(
                 {"error": "Server has no SSH credentials configured for updates."},
                 status=status.HTTP_400_BAD_REQUEST,
             )

        # Reset status and set log header for update
        server.provision_status = ManagedServer.ProvisionStatus.PENDING
        server.provision_logs = f"--- Update started by {request.user.username} at {timezone.now()} ---\n"
        server.save(update_fields=["provision_status", "provision_logs", "updated_at"])

        # Use the same provision task (idempotent), but skip post-install reboot
        from .services.provisioner import provision_server
        provision_server.delay(str(server.id), skip_reboot=True)

        return Response(
            ManagedServerSerializer(server).data,
            status=status.HTTP_202_ACCEPTED,
        )

    # ── Agent Self-Registration (lite-agent registrar) ──────────────────
    #
    # The lite agent's registrar (a small service running inside the
    # agent's docker-compose stack) calls these endpoints to tell the
    # master "I am booted and ready" (one-shot) and "I am still alive"
    # (every 30s). Both are HMAC-authenticated with the
    # server.gateway_secret that the master provisioned into the
    # agent's .env during install. They never require an admin
    # session — that way the agent can self-register before the
    # operator has logged in to the platform.

    @action(
        detail=True,
        methods=["post"],
        url_path="agent-ready",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def agent_ready(self, request, pk=None):
        """
        Mark this node as ``agent_ready=True`` and stamp its runtime
        info from the agent's first successful boot.

        The agent-registrar service on the lite node calls this once
        it has finished installing and confirmed all of its own
        containers are healthy. Authentication is via the
        per-server ``gateway_secret`` HMAC (not a user session) so
        the agent can register itself before any operator has
        logged in to the platform.
        """
        from apps.deployments.services.agent_registrar_auth import (
            verify_agent_hmac,
        )

        server = self.get_object()
        if not verify_agent_hmac(request, server):
            return Response(
                {"error": "Invalid or missing HMAC signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        runtime_info = {}
        if isinstance(request.data, dict):
            runtime_info = request.data.get("runtime_info") or {}
            if not isinstance(runtime_info, dict):
                runtime_info = {}

        update_fields = ["agent_ready", "last_agent_heartbeat_at", "updated_at"]
        server.agent_ready = True
        server.last_agent_heartbeat_at = timezone.now()
        if runtime_info:
            server.agent_runtime_info = runtime_info
            update_fields.append("agent_runtime_info")
        # If the agent reports it is ready, mark the provision as done
        if server.provision_status in {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
        }:
            server.provision_status = ManagedServer.ProvisionStatus.DONE
            update_fields.append("provision_status")
        server.save(update_fields=update_fields)

        _append_log_safe(
            server,
            f"✅ Agent ready: runtime={_truncate_dict(runtime_info)}",
        )

        return Response({
            "status": "ok",
            "agent_ready": True,
            "server_id": str(server.id),
            "node_id": runtime_info.get("node_id", ""),
            "master_time": timezone.now().isoformat(),
        })

    @action(
        detail=True,
        methods=["post"],
        url_path="agent-heartbeat",
        permission_classes=[],
        authentication_classes=[],
        throttle_classes=[],
    )
    def agent_heartbeat(self, request, pk=None):
        """
        Receive a periodic heartbeat from the agent's registrar.

        The agent posts every 30s with:

        * ``runtime_info``: docker version, image versions, host
          uptime, disk/mem (refreshed on every heartbeat)
        * ``status``: free-form health string (e.g. ``"ok"``,
          ``"degraded"``)
        * ``queues``: dict of celery queue depths (best-effort,
          empty on lite agents that don't have access to the
          master queue)
        """
        from apps.deployments.services.agent_registrar_auth import (
            verify_agent_hmac,
        )

        server = self.get_object()
        if not verify_agent_hmac(request, server):
            return Response(
                {"error": "Invalid or missing HMAC signature."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        runtime_info = {}
        if isinstance(request.data, dict):
            runtime_info = request.data.get("runtime_info") or {}
            if not isinstance(runtime_info, dict):
                runtime_info = {}

        status_payload = ""
        if isinstance(request.data, dict):
            status_payload = str(request.data.get("status", "") or "").strip()

        update_fields = [
            "last_agent_heartbeat_at",
            "agent_runtime_info",
            "agent_ready",
            "updated_at",
        ]
        server.last_agent_heartbeat_at = timezone.now()
        if runtime_info:
            server.agent_runtime_info = runtime_info
        # Heartbeats always confirm readiness — the agent is alive
        # and reporting in, even if the operator hasn't manually
        # marked the node ready. The first heartbeat implicitly
        # asserts readiness.
        if not server.agent_ready:
            server.agent_ready = True
            _append_log_safe(
                server,
                "✅ Agent ready (implicit via first heartbeat)",
            )
        server.save(update_fields=update_fields)

        # Update status if heartbeat says "degraded" or "down" so
        # operators see the agent's self-reported state in the
        # dashboard. Only override ONLINE; never auto-promote to
        # ONLINE from a heartbeat alone.
        if status_payload.lower() in {"degraded", "down", "unhealthy"}:
            if server.status == ManagedServer.Status.ONLINE:
                server.status = ManagedServer.Status.DEGRADED
                server.save(update_fields=["status", "updated_at"])

        return Response({
            "status": "ok",
            "server_id": str(server.id),
            "master_time": timezone.now().isoformat(),
        })

    # ── Health Check ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"],
            throttle_classes=[ServerHealthCheckRateThrottle])
    def health_check(self, request, pk=None):
        """Ping a remote server's API to check if it's online."""
        server = self.get_object()
        server = _refresh_managed_server_health(server)
        return Response(ManagedServerSerializer(server).data)

    @action(detail=False, methods=["post"])
    @throttle_classes([ServerCheckAllThrottle])
    def check_all(self, request):
        """Health check all servers — dispatched to Celery for parallelism."""
        from .tasks_health import refresh_managed_server_health
        servers = list(self.get_queryset())
        for server in servers:
            refresh_managed_server_health.delay(str(server.id))
        return Response(
            {"status": "scheduled", "count": len(servers)},
            status=status.HTTP_202_ACCEPTED,
        )

    # ── Proxy ────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerProxyThrottle])
    def proxy(self, request, pk=None):
        """
        Forward an API request to a remote server.
        Body: { "method": "GET", "path": "/api/v1/services/", "body": null }
        """
        import json as json_mod
        import posixpath
        from urllib.parse import urlparse

        MAX_PROXY_BODY_SIZE = 1_048_576  # 1MB

        server = self.get_object()
        method = request.data.get("method", "GET").upper()
        if method not in ALLOWED_PROXY_METHODS:
            return Response(
                {"error": f"Method {method} is not allowed."},
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        raw_path = str(request.data.get("path", "") or "")
        body = request.data.get("body")

        if body is not None:
            serialized = json_mod.dumps(body, sort_keys=True)
            if len(serialized.encode('utf-8')) > MAX_PROXY_BODY_SIZE:
                return Response(
                    {"error": f"Proxy body too large; max {MAX_PROXY_BODY_SIZE} bytes."},
                    status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )

        # Preserve query params while normalizing just the path segment.
        path_part, _, query_part = raw_path.partition("?")
        normalized_path = posixpath.normpath(path_part or "/")
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        path = f"{normalized_path}?{query_part}" if query_part else normalized_path

        # Reject any path containing ".." even after normalization
        if ".." in path:
            return Response(
                {"error": "Directory traversal is not allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Constrain the proxy to a fixed allowlist of safe read-only platform
        # endpoints. This blocks tenant-controlled SSRF amplification via
        # the platform's API token. Compare the path portion only, ignoring
        # any query string so legitimate ?detail=1 etc. still work.
        path_only_for_match = normalized_path.rstrip("/")
        if not any(
            path_only_for_match == allowed.rstrip("/")
            or path_only_for_match.startswith(allowed.rstrip("/") + "/")
            for allowed in ALLOWED_PROXY_PATHS
        ):
            return Response(
                {"error": "Path not in proxy allowlist."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Re-verify /api/ prefix after normalization
        if not path.startswith("/api/"):
            return Response(
                {"error": "Only /api/ paths can be proxied."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        lite_agent_response = _lite_agent_proxy_response(server, request, method, path)
        if lite_agent_response is not None:
            return lite_agent_response

        # Validate the server URL scheme is HTTP/HTTPS (prevent file://, etc.)
        parsed = urlparse(server.api_url)
        if parsed.scheme not in ("http", "https"):
            return Response(
                {"error": "Server API URL must use HTTP or HTTPS."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # SECURITY: refuse to forward authenticated requests to a hostname
        # that does not match the registered server.host. A tenant can
        # otherwise register api_url=http://attacker.example.com and have
        # the platform ship the gateway secret / API token straight to the
        # attacker.
        api_host = (parsed.hostname or "").strip().lower()
        server_host = (server.host or "").strip().lower()
        if not server_host:
            return Response(
                {"error": "Server host is not configured."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if api_host != server_host:
            return Response(
                {
                    "error": (
                        "api_url hostname does not match server.host; "
                        "refusing to forward authenticated proxy request."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = f"{server.api_url.rstrip('/')}{path}"
        body_bytes = json_mod.dumps(body, sort_keys=True).encode() if body is not None else b""
        headers = _build_remote_headers(server, method=method, path=path, body=body_bytes)

        try:
            resp = requests.request(
                method, url,
                headers=headers,
                data=body_bytes if body is not None else None,
                timeout=30,
            )
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text[:2000]}

            return Response({
                "status_code": resp.status_code,
                "data": data,
            })
        except requests.RequestException as e:
            return _proxy_error_response(f"Proxy request failed: {e!s}")

    # ── Remote Services (convenience) ────────────────────────────────────

    @action(detail=True, methods=["get"])
    def services(self, request, pk=None):
        """Fetch services for a managed server.

        Lite agents share the master DB so we query locally.
        Full remote servers are proxied via their API.
        """
        server = self.get_object()

        # ── Lite agent: local DB query ──
        if server.is_lite_agent:
            qs = Service.objects.filter(
                server=server,
            ).exclude(
                status=Service.Status.DELETED,
            ).select_related('project').order_by('-updated_at')
            serializer = ServiceSerializer(qs, many=True, context={'request': request})
            return Response({'results': serializer.data, 'count': len(serializer.data)})

        # ── Full remote server: proxy ──
        if not server.api_url:
            return Response(_safe_remote_error_payload("services", "Server has no API URL yet."))

        api_path = "/api/v1/services/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "services", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def deployments(self, request, pk=None):
        """Fetch deployments for a managed server.

        Lite agents share the master DB so we query locally.
        Full remote servers are proxied via their API.
        """
        server = self.get_object()

        # ── Lite agent: local DB query ──
        if server.is_lite_agent:
            qs = Deployment.objects.filter(
                service__server=server,
            ).select_related('service').order_by('-created_at')[:50]
            serializer = DeploymentSerializer(qs, many=True)
            return Response({'results': serializer.data, 'count': len(serializer.data)})

        # ── Full remote server: proxy ──
        if not server.api_url:
            return Response(_safe_remote_error_payload("deployments", "Server has no API URL yet."))

        api_path = "/api/v1/deployments/"
        payload, error_payload = _fetch_remote_json_with_fallback(
            server, "deployments", api_path, timeout=15
        )
        if error_payload:
            return Response(error_payload)
        return Response(payload)

    @action(detail=True, methods=["get"])
    def domains(self, request, pk=None):
        """Aggregate all custom domains across all services on a managed server.

        Lite agents share the master DB so we query locally.
        Full remote servers are proxied via their API.
        """
        server = self.get_object()

        # ── Lite agent: local DB query ──
        if server.is_lite_agent:
            services_qs = Service.objects.filter(
                server=server,
            ).exclude(
                status=Service.Status.DELETED,
            ).only('id', 'name', 'public_domain', 'custom_domains', 'domain_verified', 'verification_token')

            domains = []
            for svc in services_qs:
                custom = svc.custom_domains if isinstance(svc.custom_domains, list) else []
                for domain in custom:
                    domains.append({
                        "domain": domain,
                        "service_id": str(svc.id),
                        "service_name": svc.name,
                        "public_domain": svc.public_domain or "",
                        "verified": svc.domain_verified,
                        "verification_token": svc.verification_token or "",
                    })
            return Response({"domains": domains, "count": len(domains)})

        # ── Full remote server: proxy ──
        if not server.api_url:
            return Response(_safe_remote_error_payload("domains", "Server has no API URL yet."))

        all_services = []
        seen_paths = set()
        next_path = "/api/v1/services/"
        max_pages = 50

        for _ in range(max_pages):
            normalized_path = _normalize_remote_api_path(next_path)
            if not normalized_path or normalized_path in seen_paths:
                break
            seen_paths.add(normalized_path)

            payload, error_payload = _fetch_remote_json_with_fallback(
                server, "domains", normalized_path, timeout=15
            )
            if error_payload:
                if not all_services:
                    return Response(error_payload)
                break

            services_page, next_link = _extract_page_results_and_next(payload)
            all_services.extend(services_page)
            if not next_link:
                break
            next_path = _normalize_remote_api_path(next_link)

        domains = []
        for svc in all_services:
            svc_id = svc.get("id", "")
            svc_name = svc.get("name", "")
            public_domain = svc.get("public_domain", "")
            custom_domains = svc.get("custom_domains", [])
            if not isinstance(custom_domains, list):
                custom_domains = []
            for domain in custom_domains:
                domains.append({
                    "domain": domain,
                    "service_id": svc_id,
                    "service_name": svc_name,
                    "public_domain": public_domain,
                    "verified": svc.get("domain_verified", False),
                    "verification_token": svc.get("verification_token", ""),
                })

        return Response({"domains": domains, "count": len(domains)})

    # ── Registry Access ───────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="registries")
    def registries(self, request, pk=None):
        """
        GET  /api/v1/servers/{id}/registries/  — list registries this node can access
        POST /api/v1/servers/{id}/registries/  — set which registries this node can access

        POST body::
            {
                "registry_ids": ["uuid1", "uuid2"]
            }

        The node's installer runs ``docker login`` for each registry
        during provisioning, so the node can pull images from them.
        """
        server = self.get_object()

        if request.method == "POST":
            registry_ids = request.data.get("registry_ids", [])
            if not isinstance(registry_ids, list):
                return Response(
                    {"error": "registry_ids must be a list of UUIDs"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Per-registry ownership check.
            #
            # Before this filter, any authenticated user could POST a list of
            # active ScopedRegistry UUIDs and attach them to their own server.
            # The node installer would then ``docker login`` with credentials
            # belonging to a different tenant. Restrict to registries whose
            # GenericForeignKey scope (Organization / Team / Project) is one
            # the requesting user has a relationship with.
            from django.contrib.contenttypes.models import ContentType
            from django.db.models import Q

            from apps.organizations.models import Organization, OrganizationMembership
            from apps.teams.models import Team, TeamMember

            from .models import ScopedRegistry
            from .models_core import Project
            from .models_project import ProjectMember

            user_org_ids = set(
                OrganizationMembership.objects
                .filter(user=request.user)
                .values_list("organization_id", flat=True)
            ) | set(
                Organization.objects.filter(owner=request.user).values_list("id", flat=True)
            )
            user_team_ids = set(
                TeamMember.objects
                .filter(user=request.user, is_active=True)
                .values_list("team_id", flat=True)
            )
            # Team owners also reach team-scoped registries.
            user_team_ids |= set(
                Team.objects.filter(owner=request.user).values_list("id", flat=True)
            )
            user_project_ids = set(
                Project.objects.filter(owner=request.user).values_list("id", flat=True)
            ) | set(
                ProjectMember.objects.filter(user=request.user).values_list("project_id", flat=True)
            )

            org_ct = ContentType.objects.get_for_model(Organization)
            team_ct = ContentType.objects.get_for_model(Team)
            project_ct = ContentType.objects.get_for_model(Project)

            accessible_scopes = (
                Q(content_type=org_ct, object_id__in=user_org_ids)
                | Q(content_type=team_ct, object_id__in=user_team_ids)
                | Q(content_type=project_ct, object_id__in=user_project_ids)
            )

            registries = (
                ScopedRegistry.objects
                .filter(id__in=registry_ids, is_active=True)
                .filter(accessible_scopes)
            )
            if len(registries) != len(registry_ids):
                # Collapse "missing / inactive / inaccessible" into one opaque
                # 400 so users cannot enumerate which IDs exist by probing.
                return Response(
                    {"error": "One or more registry IDs are invalid, inactive, or inaccessible"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            server.registry_access.set(registries)
            logger.info(
                "Set %d registries for server %s (%s)",
                len(registries), server.name, server.id,
            )
            return Response({
                "status": "ok",
                "registry_ids": [str(r.id) for r in registries],
            })

        # GET: return registries this node can access
        registries = server.registry_access.filter(is_active=True)
        return Response({
            "count": registries.count(),
            "registries": [
                {
                    "id": str(r.id),
                    "registry_url": r.registry_url,
                    "is_internal": r.is_internal,
                    "scope_type": r.content_type.model if r.content_type else None,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in registries
            ],
        })

    # ── Self-Healing ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerHealThrottle])
    def heal(self, request, pk=None):
        """
        Trigger self-healing on a remote server.

        Body (optional):
        {
            "deployment_id": "uuid",  // specific deployment to heal
            "action": "restart_container" | "restart_stack" | "restart_docker_daemon" | "diagnose" | "full"
        }

        If no deployment_id is provided, runs node-level diagnostics and healing.
        """
        server = self.get_object()

        if not server.ssh_key and not server.ssh_password:
            return Response(
                {"error": "No SSH credentials stored for this server"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = request.data.get("action", "full")
        deployment_id = request.data.get("deployment_id")

        if action == "diagnose":
            return self._run_diagnostics(server)

        if deployment_id:
            try:
                from apps.deployments.models_core import Deployment
                deployment = Deployment.objects.get(id=deployment_id)
            except (Deployment.DoesNotExist, ValueError):
                return Response(
                    {"error": f"Deployment {deployment_id} not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

            from apps.deployments.tasks_deploy_remote import self_heal_remote_deployment
            self_heal_remote_deployment.delay(
                deployment_id=str(deployment.id),
                server_id=str(server.id),
            )
            return Response({
                "status": "healing_triggered",
                "deployment_id": str(deployment.id),
                "message": "Self-healing task queued",
            })

        if action in ("restart_container", "restart_docker_daemon", "restart_stack", "full"):
            return self._trigger_node_healing(server, action)

        return Response(
            {"error": f"Unknown action: {action}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=["get", "post"])
    def diagnostics(self, request, pk=None):
        """
        Get current diagnostics for a remote server.

        Returns Docker status, resource usage, container states, etc.
        """
        server = self.get_object()
        return self._run_diagnostics(server)

    @action(detail=True, methods=["post"])
    @throttle_classes([ServerCommandThrottle])
    def run_command(self, request, pk=None):
        """
        Run a diagnostic/recovery command on a remote server via SSH.

        Body: { "command": "docker ps -a" }

        Only allows safe diagnostic and recovery commands.
        """
        server = self.get_object()

        if not server.ssh_key and not server.ssh_password:
            return Response(
                {"error": "No SSH credentials stored for this server"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        command = request.data.get("command", "").strip()
        if not command:
            return Response(
                {"error": "Command is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _is_command_allowed(command):
            return Response(
                {"error": "Command not allowed. Only safe docker subcommands (ps, logs, stats, inspect, images, info, version, df, top, port, events) and system diagnostic commands are permitted."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            from apps.deployments.services.self_healing_orchestrator import (
                SelfHealingOrchestrator,
            )
            orchestrator = SelfHealingOrchestrator(server)
            out, err, code = orchestrator._exec(command, timeout=60)
            orchestrator._close_ssh()

            try:
                redacted_out = _redact_transfer_text(out or "")
            except Exception as exc:
                logger.error("Redaction failed for run_command stdout: %s", exc)
                redacted_out = "[REDACTION FAILED — output suppressed for safety]"
            try:
                redacted_err = _redact_transfer_text(err or "")
            except Exception as exc:
                logger.error("Redaction failed for run_command stderr: %s", exc)
                redacted_err = "[REDACTION FAILED — output suppressed for safety]"

            return Response({
                "command": command,
                "exit_code": code,
                "stdout": redacted_out[:10000],
                "stderr": redacted_err[:5000],
            })
        except Exception as exc:
            return Response(
                {"error": f"Command execution failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['get'], url_path='incident-report')
    def incident_report(self, request, pk=None):
        """Aggregate server-level incident report.

        GET /api/v1/servers/{id}/incident-report/

        Returns all incidents affecting this server: failed deployments,
        health transitions, provisioning failures, transfer failures,
        service lifecycle events, and mesh/network problems.
        """
        server = self.get_object()
        from django.db.models import Q
        from apps.deployments.models_audit import AuditLog
        from apps.deployments.models_backup import ServiceBackup
        from apps.deployments.models_core import Deployment, Service
        from apps.deployments.models_transfer import ServerTransfer

        events: list = []
        server_name = server.name or server.host or str(server.id)

        # ── 1. Failed deployments on this server ─────────────────────
        failure_statuses = [
            'FAILED', 'CANCELLED', 'BUILD_FAILED', 'BACKUP_FAILED',
            'MIGRATION_FAILED', 'HEALTH_CHECK_FAILED',
        ]
        services = Service.objects.filter(server=server)
        failed_deploys = (
            Deployment.objects
            .filter(service__in=services, status__in=failure_statuses)
            .select_related('service')
            .order_by('-created_at')[:30]
        )
        for d in failed_deploys:
            events.append({
                'type': 'deployment',
                'severity': 'critical' if d.status == 'FAILED' else 'warning',
                'timestamp': d.created_at.isoformat() if d.created_at else '',
                'title': f"{d.service.name}: deployment {d.status.lower().replace('_', ' ')}",
                'detail': (d.commit_message or '')[:500],
                'service_id': str(d.service_id),
                'service_name': d.service.name,
                'deployment_id': str(d.id),
                'status': d.status,
            })

        # ── 2. Failed backups on this server ─────────────────────────
        failed_backups = (
            ServiceBackup.objects
            .filter(service__in=services, status='FAILED')
            .select_related('service')
            .order_by('-created_at')[:10]
        )
        for b in failed_backups:
            events.append({
                'type': 'backup_failure',
                'severity': 'warning',
                'timestamp': b.created_at.isoformat() if b.created_at else '',
                'title': f"{b.service.name}: backup failed",
                'detail': b.error_message or '',
                'service_id': str(b.service_id),
                'backup_id': str(b.id),
            })

        # ── 3. Health transitions on this server ─────────────────────
        health_actions = [
            'HEALTH_TRANSITION', 'SERVICE_HEALTHY', 'SERVICE_UNHEALTHY',
        ]
        service_ids = [str(s.id) for s in services]
        health_audits = []
        if service_ids:
            from django.db.models import Q as QQ
            health_filter = QQ()
            for sid in service_ids:
                health_filter |= QQ(metadata__contains={'service_id': sid})
            health_audits = list(
                AuditLog.objects
                .filter(health_filter)
                .filter(action__in=health_actions)
                .order_by('-timestamp')[:20]
            )
        for a in health_audits:
            previous = (a.metadata or {}).get('previous', '')
            current = (a.metadata or {}).get('current', '')
            events.append({
                'type': 'health',
                'severity': (
                    'critical' if a.action == 'SERVICE_UNHEALTHY' else 'warning'
                ),
                'timestamp': a.timestamp.isoformat() if a.timestamp else '',
                'title': f'{previous} → {current}' if previous and current else a.action.replace('_', ' ').title(),
                'detail': (a.metadata or {}).get('message', ''),
                'actor': a.actor,
                'action': a.action,
            })

        # ── 4. Transfers involving this server ───────────────────────
        transfers = (
            ServerTransfer.objects
            .filter(
                Q(source_server=server) | Q(target_server=server),
            )
            .exclude(status='COMPLETED')
            .order_by('-created_at')[:10]
        )
        for t in transfers:
            events.append({
                'type': 'transfer',
                'severity': 'critical' if t.status == 'FAILED' else 'warning',
                'timestamp': t.created_at.isoformat() if t.created_at else '',
                'title': f"Server transfer {t.status.lower()}",
                'detail': t.error_message or f'Source → Target',
                'transfer_id': str(t.id),
                'status': t.status,
            })

        # ── 5. Provisioning failures ─────────────────────────────────
        prov_logs = getattr(server, 'provision_logs', '') or ''
        if prov_logs:
            prov_lines = prov_logs.split('\n')
            for line in reversed(prov_lines[-20:]):
                lower = line.strip().lower()
                if not lower:
                    continue
                if 'error' in lower or 'fail' in lower or 'exception' in lower:
                    events.append({
                        'type': 'provisioning',
                        'severity': 'warning',
                        'timestamp': '',
                        'title': 'Provisioning error detected',
                        'detail': line.strip()[:300],
                    })

        # ── 6. Server metadata ──────────────────────────────────────
        service_list = [
            {'id': str(s.id), 'name': s.name, 'status': s.status}
            for s in services
        ]
        active_count = sum(1 for s in services if s.status == 'ACTIVE')

        events.sort(key=lambda e: e['timestamp'] or '', reverse=True)

        severity_counts = {'critical': 0, 'warning': 0, 'info': 0}
        for e in events:
            sev = e.get('severity', 'info')
            if sev in severity_counts:
                severity_counts[sev] += 1

        return Response({
            'server_id': str(server.id),
            'server_name': server_name,
            'server_status': server.status,
            'total_services': len(service_list),
            'active_services': active_count,
            'total_events': len(events),
            'critical': severity_counts['critical'],
            'warning': severity_counts['warning'],
            'info': severity_counts['info'],
            'services': service_list,
            'events': events,
        })

    def _run_diagnostics(self, server):
        try:
            from apps.deployments.services.self_healing_orchestrator import (
                SelfHealingOrchestrator,
            )
            orchestrator = SelfHealingOrchestrator(server)
            diagnostics = orchestrator.run_full_diagnostics()
            orchestrator._close_ssh()

            return Response({
                "server": {
                    "id": str(server.id),
                    "name": server.name,
                    "host": server.host,
                },
                "docker_running": diagnostics.docker_running,
                "disk_usage_pct": diagnostics.disk_usage_pct,
                "memory_usage_pct": diagnostics.memory_usage_pct,
                "network_reachable": diagnostics.network_reachable,
                "failure_type": diagnostics.failure_type.value,
                "container_state": diagnostics.container_state,
                "error_details": diagnostics.error_details,
                "suggested_actions": [a.value for a in diagnostics.suggested_actions],
                "exited_containers": diagnostics.raw_diagnostics.get("exited_containers", ""),
            })
        except Exception as exc:
            return Response(
                {"error": f"Diagnostics failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _trigger_node_healing(self, server, action: str):
        """Trigger node-level healing actions."""
        try:
            from apps.deployments.services.self_healing_orchestrator import (
                RecoveryAction,
                SelfHealingOrchestrator,
            )

            orchestrator = SelfHealingOrchestrator(server)

            action_map = {
                "restart_container": RecoveryAction.RESTART_CONTAINER,
                "restart_docker_daemon": RecoveryAction.RESTART_DOCKER_DAEMON,
                "restart_stack": RecoveryAction.RESTART_STACK,
                "full": RecoveryAction.RESTART_STACK,
            }
            recovery_action = action_map.get(action, RecoveryAction.RESTART_STACK)

            class _FakeDeployment:
                id = "manual"
                container_id = ""
                service = type("obj", (object,), {"name": ""})()

            result = orchestrator._execute_recovery(
                recovery_action, _FakeDeployment(), orchestrator._diagnostics
            )
            orchestrator._close_ssh()

            return Response({
                "action": recovery_action.value,
                "success": result.success,
                "details": result.details,
                "post_recovery_status": result.post_recovery_status,
                "next_action": result.next_action.value if result.next_action else None,
                "heal_log": orchestrator.get_heal_log()[-10:],
            })
        except Exception as exc:
            return Response(
                {"error": f"Healing failed: {exc!s}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
