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
import time
import os
import uuid
from typing import Any
from urllib.parse import urlparse

import requests
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models_servers import ManagedServer
from .models_core import Service, Deployment
from .serializers import ServiceSerializer, DeploymentSerializer

logger = logging.getLogger(__name__)

MANAGED_SERVER_HEALTH_TIMEOUT = 10


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
            _append_unique(urls, f"http://{wg_ip}")
            _append_unique(urls, f"http://{wg_ip}:8090")
        else:
            _append_unique(urls, f"http://{wg_ip}:8090")
            _append_unique(urls, f"http://{wg_ip}")

    if not host_port:
        return urls

    has_explicit_port = host_port.count(":") == 1

    # ── Priority 2: Public IP / Domain (fallback) ──
    if _server_host_is_ip(host_port):
        if is_lite:
            _append_unique(urls, f"http://{host_port}")
            _append_unique(urls, f"http://{host_port}:8090")
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
    for base_url in _candidate_api_urls(server):
        for health_path in health_paths:
            try:
                # We use verify=False because many remote nodes use self-signed certs
                # or haven't finished provisioning SSL via Caddy yet.
                response = requests.get(
                    f"{base_url}{health_path}",
                    timeout=MANAGED_SERVER_HEALTH_TIMEOUT,
                    verify=False,
                )
            except requests.RequestException:
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

                resp = requests.post(
                    f"{url_base}{path}",
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Gateway-Signature-V2": sig,
                        "X-Request-Timestamp": ts,
                    },
                    timeout=15,
                    verify=False,
                )
                if resp.status_code == 200:
                    token = resp.json().get("token")
                    if token:
                        logger.info("Auto-exchanged HMAC for API token on %s via %s", server.host, url_base)
                        return token
            except Exception as exc:
                logger.debug("HMAC token exchange failed for %s via %s: %s", server.host, url_base, exc)

        allow_pw_exchange = str(os.environ.get("ALLOW_REMOTE_PASSWORD_EXCHANGE", "")).lower() in {
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
                        verify=False,
                    )
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
                        verify=False,
                    )
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
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body if isinstance(body, bytes) else b"").hexdigest()
        payload = f"{method}|{path}|{timestamp}|{body_hash}"
        signature = hmac_mod.new(
            gateway_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Gateway-Signature-V2"] = signature
        headers["X-Request-Timestamp"] = timestamp
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

    last_error = None
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
                last_error = str(exc)
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
        qs = (
            Service.objects
            .filter(server=server)
            .exclude(status=Service.Status.DELETED)
            .select_related("project")
            .order_by("-updated_at")
        )
        data = ServiceSerializer(qs, many=True, context={"request": request}).data
        return Response({"status_code": 200, "data": {"results": data, "count": len(data)}})

    if path_only == "/api/v1/deployments":
        qs = (
            Deployment.objects
            .filter(service__server=server)
            .select_related("service")
            .order_by("-created_at")[:50]
        )
        data = DeploymentSerializer(qs, many=True).data
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


# --- Serializers -------------------------------------------------------------
class ManagedServerSerializer(serializers.ModelSerializer):
    has_ssh_credentials = serializers.SerializerMethodField()

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
        ]
        read_only_fields = [
            "id", "status", "last_health_check", "server_version",
            "services_count", "created_at", "provision_status",
            "role", "wg_address", "has_ssh_credentials", "is_lite_agent",
        ]


class ManagedServerCreateSerializer(serializers.ModelSerializer):
    """For 'Connect Existing' mode — user provides api_url + api_token."""
    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "private_ip", "api_url", "api_token",
            "gateway_secret", "ssh_user", "ssh_password", "ssh_key",
            "ssh_port", "is_primary", "allow_user_workloads",
            "provider_metadata", "is_lite_agent",
        ]
        extra_kwargs = {
            "api_token": {"write_only": True, "required": False},
            "gateway_secret": {"write_only": True, "required": False},
            "ssh_key": {"write_only": True, "required": False, "trim_whitespace": False},
            "ssh_password": {"write_only": True, "required": False},
            "provider_metadata": {"required": False},
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

    def to_representation(self, instance):
        # Return the stable read serializer shape after create/update operations.
        return ManagedServerSerializer(instance).data

    def validate_host(self, value):
        """Strip protocol and port from host — should be bare IP or domain."""
        import re
        value = re.sub(r'^https?://', '', value).strip().rstrip('/')
        value = re.sub(r':\d+$', '', value)
        return value

    def validate_api_url(self, value):
        """Ensure api_url has a protocol prefix. Default to HTTP for IPs."""
        value = value.strip().rstrip('/')
        if value and not value.startswith(('http://', 'https://')):
            # Detect bare IP and default to HTTP
            host_part = value.split(':')[0]
            try:
                ipaddress.ip_address(host_part)
                value = f'http://{value}'
            except ValueError:
                value = f'https://{value}'
        return value


class ManagedServerProvisionSerializer(serializers.ModelSerializer):
    """For 'Provision New' mode — user provides SSH credentials."""
    ssh_auth_method = serializers.ChoiceField(
        choices=["password", "key"], write_only=True,
    )

    class Meta:
        model = ManagedServer
        fields = [
            "name", "host", "ssh_port", "ssh_user",
            "ssh_password", "ssh_key", "ssh_auth_method",
            "is_primary", "allow_user_workloads", "is_lite_agent",
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
        return data


# ─── ViewSet ─────────────────────────────────────────────────────────────────

class ManagedServerViewSet(viewsets.ModelViewSet):
    """CRUD for managed remote servers, plus health check, proxy, and provisioning."""

    queryset = ManagedServer.objects.all()
    permission_classes = [IsAuthenticated]

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
    def provision_new(self, request):
        """
        Create a server record and kick off auto-provisioning via SSH.
        The installer will run on the remote VPS and auto-fill api_url/api_token.
        """
        serializer = ManagedServerProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Remove the non-model field before saving
        validated = serializer.validated_data.copy()
        validated.pop("ssh_auth_method", None)

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

    @action(detail=True, methods=["get"], url_path="provision-logs")
    def provision_logs(self, request, pk=None):
        """Get the provisioning logs for a server."""
        server = self.get_object()
        return Response({
            "provision_status": server.provision_status,
            "provision_logs": server.provision_logs,
        })

    @action(detail=True, methods=["post"], url_path="retry-provision")
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
    def update_server(self, request, pk=None):
        """
        Trigger a remote update (git pull + restart) on a managed server.
        """
        server = self.get_object()

        blocked_statuses = {
            ManagedServer.ProvisionStatus.PENDING,
            ManagedServer.ProvisionStatus.PROVISIONING,
            ManagedServer.ProvisionStatus.UPDATING,
        }
        if server.provision_status in blocked_statuses:
            logger.warning(
                "Auto-clearing stalled provision_status=%s for server %s",
                server.provision_status,
                server.id,
            )
            server.provision_status = ManagedServer.ProvisionStatus.READY
            server.save(update_fields=["provision_status", "updated_at"])

        # We only allow updates on nodes that have been provisioned or have SSH access
        if not (server.ssh_key or server.ssh_password):
             return Response(
                 {"error": "Server has no SSH credentials configured for updates."},
                 status=status.HTTP_400_BAD_REQUEST,
             )

        server.provision_logs = (
            (server.provision_logs or "")
            + f"\n--- Update queued by {request.user.username} at {timezone.now()} ---\n"
        )
        server.save(update_fields=["provision_logs", "updated_at"])

        from .tasks import update_remote_server_task
        try:
            update_remote_server_task.delay(str(server.id))
        except Exception as exc:  # pragma: no cover - broker/runtime failure
            logger.exception("Failed to queue update for server %s", server.id)
            server.provision_status = ManagedServer.ProvisionStatus.FAILED
            server.provision_logs = (
                (server.provision_logs or "")
                + f"\nFATAL ERROR: failed to queue update task: {exc}\n"
            )
            server.save(update_fields=["provision_status", "provision_logs", "updated_at"])
            return Response(
                {
                    "error": "Failed to queue server update task. Check Celery/Redis health.",
                    "server_id": str(server.id),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            "success": True,
            "message": "Update task queued. Progress will be visible in provision logs.",
            "server_id": str(server.id),
            "provision_status": server.provision_status,
        })

    # ── Health Check ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def health_check(self, request, pk=None):
        """Ping a remote server's API to check if it's online."""
        server = self.get_object()
        server = _refresh_managed_server_health(server)
        return Response(ManagedServerSerializer(server).data)

    @action(detail=False, methods=["post"])
    def check_all(self, request):
        """Health check all servers at once."""
        servers = self.get_queryset()
        results = []
        for server in servers:
            server = _refresh_managed_server_health(server)
            results.append(ManagedServerSerializer(server).data)

        return Response({"servers": results})

    # ── Proxy ────────────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
    def proxy(self, request, pk=None):
        """
        Forward an API request to a remote server.
        Body: { "method": "GET", "path": "/api/v1/services/", "body": null }
        """
        import posixpath
        import json as json_mod
        from urllib.parse import urlparse

        server = self.get_object()
        method = request.data.get("method", "GET").upper()
        raw_path = str(request.data.get("path", "") or "")
        body = request.data.get("body")

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

        # Re-verify /api/ prefix after normalization
        if not path.startswith("/api/"):
            return Response(
                {"error": "Only /api/ paths can be proxied."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate only HTTP/HTTPS methods are allowed
        allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
        if method not in allowed_methods:
            return Response(
                {"error": f"Method {method} is not allowed."},
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
            return _proxy_error_response(f"Proxy request failed: {str(e)}")

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

    # ── Self-Healing ─────────────────────────────────────────────────────

    @action(detail=True, methods=["post"])
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

            from apps.deployments.tasks import self_heal_remote_deployment
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

    @action(detail=True, methods=["get"])
    def diagnostics(self, request, pk=None):
        """
        Get current diagnostics for a remote server.

        Returns Docker status, resource usage, container states, etc.
        """
        server = self.get_object()
        return self._run_diagnostics(server)

    @action(detail=True, methods=["post"])
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

        allowed_prefixes = (
            "docker ", "cd /opt/smsly-hosting && docker ",
            "df ", "free ", "ping ", "systemctl status docker",
            "cat /opt/smsly-hosting/.env | grep -v SECRET | grep -v PASSWORD | grep -v KEY",
        )
        if not any(command.startswith(p) for p in allowed_prefixes):
            return Response(
                {"error": "Command not allowed. Only Docker, diagnostic, and safe recovery commands are permitted."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            from apps.deployments.services.self_healing_orchestrator import SelfHealingOrchestrator
            orchestrator = SelfHealingOrchestrator(server)
            out, err, code = orchestrator._exec(command, timeout=60)
            orchestrator._close_ssh()

            return Response({
                "command": command,
                "exit_code": code,
                "stdout": out[:10000],
                "stderr": err[:5000],
            })
        except Exception as exc:
            return Response(
                {"error": f"Command execution failed: {str(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _run_diagnostics(self, server):
        """Run diagnostics on a remote server and return results."""
        try:
            from apps.deployments.services.self_healing_orchestrator import SelfHealingOrchestrator
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
                {"error": f"Diagnostics failed: {str(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _trigger_node_healing(self, server, action: str):
        """Trigger node-level healing actions."""
        try:
            from apps.deployments.services.self_healing_orchestrator import (
                SelfHealingOrchestrator,
                RecoveryAction,
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
                {"error": f"Healing failed: {str(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
