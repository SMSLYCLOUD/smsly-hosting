"""
Helper functions for multi-server management views.
"""

import contextlib
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
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework.response import Response

from ...models.core import Deployment, Service
from ...models.servers import ManagedServer
from ...serializers import DeploymentSerializer, ServiceSerializer
from apps.deployments.services.transfer_service.helpers import _redact_transfer_text

logger = logging.getLogger(__name__)

MANAGED_SERVER_HEALTH_TIMEOUT = 10
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
        from apps.deployments.services.provisioner import _append_log
        _append_log(server, message)
    except Exception:
        # If provisioner imports cycle, fall back to writing directly
        # to the model. Still best-effort.
        try:
            from django.utils import timezone as _tz
            line = f"[{_tz.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
            server.provision_logs = (server.provision_logs or "") + line + "\n"
            server.save(update_fields=["provision_logs", "updated_at"])
        except Exception as exc:
            logger.debug("Failed to append provision log: %s", exc)


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



def _detect_reachable_api_url(server) -> tuple[str | None, dict | None]:
    """Probe candidate base URLs and return the first one that responds.

    Returns (base_url, health_payload) where health_payload is the parsed
    JSON dict from the /health endpoint, or (None, None) if unreachable.
    The HTTP response is always closed inside this function to prevent
    connection leaks.
    """
    health_paths = ("/health", "/health/live")
    from apps.deployments.services.tls_verify import _check_pin_after_handshake, resolve_tls_verify
    verify, fingerprint = resolve_tls_verify(server)
    for base_url in _candidate_api_urls(server):
        for health_path in health_paths:
            response = None
            try:
                response = requests.get(
                    f"{base_url}{health_path}",
                    timeout=MANAGED_SERVER_HEALTH_TIMEOUT,
                    verify=verify,
                    stream=True,
                )
                if fingerprint:
                    _check_pin_after_handshake(response, fingerprint)

                if response.status_code >= 500:
                    continue

                # Parse the payload while the connection is still open
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                return base_url, payload

            except (requests.RequestException, ssl.SSLError):
                continue
            finally:
                if response is not None:
                    with contextlib.suppress(Exception):
                        response.close()

    return None, None


def _refresh_managed_server_health(server):
    """
    Detect a reachable API URL, update server health fields, and sync service count.
    """
    base, health_payload = _detect_reachable_api_url(server)
    update_fields = {"status", "last_health_check", "services_count"}

    if base:
        if server.api_url != base:
            server.api_url = base
            update_fields.add("api_url")

        server.status = ManagedServer.Status.ONLINE

        if isinstance(health_payload, dict):
            version = str(health_payload.get("version") or "").strip()
            if version and server.server_version != version:
                server.server_version = version
                update_fields.add("server_version")

        # Lite agents share the master DB — count services locally
        if getattr(server, 'is_lite_agent', False):
            server.services_count = Service.objects.filter(server=server).count()
        else:
            api_path = "/api/v1/services/"
            headers = _build_remote_headers(server, method="GET", path=api_path)

            # Skip the request entirely when no auth credentials exist.
            # Sending an unauthenticated request to a production endpoint
            # is both useless (it 401s) and noisy (appears in access logs
            # as an auth failure every 5 minutes).
            if not headers.get("Authorization") and not headers.get("X-Gateway-Signature-V2"):
                logger.debug(
                    "Skipping service count sync for %s — no api_token or gateway_secret",
                    server.name,
                )
            else:
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
                if not headers.get("Authorization") and not headers.get("X-Gateway-Signature-V2"):
                    logger.debug("Skipping post-exchange service count sync for %s — no auth", server.name)
                else:
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
            logger.error("Automatic VPN mesh setup failed for %s: %s", server.id, exc)

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

                from apps.deployments.services.tls_verify import (
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
                except Exception as exc:
                    logger.debug("Token exchange failed for %s: %s", url_base, exc)

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
            .select_related("project", "owner", "server")
            .prefetch_related(
                Prefetch(
                    'deployments',
                    queryset=Deployment.objects.filter(
                        status=Deployment.Status.ACTIVE
                    ).order_by('-created_at')[:1],
                    to_attr='_active_deployments',
                ),
                Prefetch(
                    'deployments',
                    queryset=Deployment.objects.order_by('-created_at')[:1],
                    to_attr='_prefetched_deployments',
                ),
                'domain_instances',
                'env_vars',
            )
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


