"""
Client for the local `grid-cryptd` Go cryptographic controller daemon.

Provides opt-in hierarchical key management (MK -> weekly SMK -> per-service SEK),
encrypted field/backup/volume operations, and Postgres/Redis point-of-contact
registration and locking. When `CRYPTD_ENABLED` is false (default unless enabled)
or `SMSLY_CRYPTD_URL` is unset, helpers degrade cleanly to no-op/legacy behavior so
users and deployments can choose whether to use `grid-cryptd` or not.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _parse_bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    v = str(val).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def rewrite_gate_url(raw_url: str, gate_host: str) -> str:
    """Replace scheme netloc host:port while preserving credentials, path, query, and fragment.

    When the source URL uses ``rediss://`` (upstream TLS Redis), downgrade the
    app-to-gate hop to plain ``redis://`` and strip ``ssl_cert_reqs`` so the
    app does not attempt TLS against cryptd's plain RESP listener (cryptd
    handles upstream TLS via ``upstream_tls``).
    """
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(raw_url)
    if parsed.username and parsed.password:
        auth = f"{parsed.username}:{parsed.password}@"
    elif parsed.username:
        auth = f"{parsed.username}@"
    elif parsed.password:
        auth = f":{parsed.password}@"
    else:
        auth = ""

    scheme = parsed.scheme
    query = parsed.query
    if scheme.lower() == "rediss":
        scheme = "redis"
        if query:
            filtered = [
                (k, v)
                for k, v in parse_qsl(query, keep_blank_values=True)
                if k.lower() != "ssl_cert_reqs"
            ]
            query = urlencode(filtered)

    return urlunparse(parsed._replace(scheme=scheme, netloc=f"{auth}{gate_host}", query=query))


def needs_host_gateway(env_vars: dict[str, Any] | None) -> bool:
    """True when a container needs host.docker.internal routed to the Docker host gateway."""
    if not env_vars:
        return False
    if env_vars.get("GRID_CRYPTD_SERVICE_ID"):
        return True
    return any(
        "host.docker.internal" in str(env_vars.get(k, ""))
        for k in (
            "DATABASE_URL",
            "REDIS_URL",
            "CELERY_BROKER_URL",
            "CELERY_RESULT_BACKEND",
            "CACHE_URL",
            "DB_HOST",
            "POSTGRES_HOST",
        )
    )


def _rewrite_gate_vars_in_env(
    env_vars: dict[str, str],
    pg_gate: str,
    redis_gate: str,
) -> None:
    """Rewrite DB & Redis URLs and derived host/port variables onto gate endpoints."""
    from urllib.parse import urlparse

    if pg_gate:
        parsed_gate = urlparse(f"//{pg_gate}")
        gate_host = parsed_gate.hostname or pg_gate.split(":", 1)[0]
        gate_port = str(parsed_gate.port) if parsed_gate.port else ""
        pg_url_keys = [
            "DATABASE_URL",
            "DB_URL",
            "POSTGRESQL_URL",
            "PG_URL",
            "POSTGRES_URL",
            "SQLALCHEMY_DATABASE_URL",
            "SQLALCHEMY_DATABASE_URI",
        ]
        for k in list(env_vars.keys()):
            if k.endswith("_POSTGRES_URL") and k not in pg_url_keys:
                pg_url_keys.append(k)
        has_pg = any(str(env_vars.get(dk, "") or "").startswith("postgres") for dk in pg_url_keys)
        for dk in pg_url_keys:
            dv = str(env_vars.get(dk, "") or "")
            if dv.startswith("postgres"):
                env_vars[dk] = rewrite_gate_url(dv, pg_gate)
        if has_pg or "DB_HOST" in env_vars or "POSTGRES_HOST" in env_vars:
            for hk in list(env_vars.keys()):
                if hk in ("DB_HOST", "POSTGRES_HOST") or hk.endswith("_POSTGRES_HOST"):
                    env_vars[hk] = gate_host
            if gate_port:
                for pk in list(env_vars.keys()):
                    if pk in ("DB_PORT", "POSTGRES_PORT") or pk.endswith("_POSTGRES_PORT"):
                        env_vars[pk] = gate_port

    if redis_gate:
        for rk in (
            "REDIS_URL",
            "REDIS_URI",
            "RATE_LIMIT_REDIS_URL",
            "SESSION_REDIS_URL",
            "CELERY_BROKER_URL",
            "CELERY_RESULT_BACKEND",
            "CACHE_URL",
        ):
            rv = str(env_vars.get(rk, "") or "")
            if rv.startswith("redis"):
                env_vars[rk] = rewrite_gate_url(rv, redis_gate)


def _resolve_mesh_gate_addr(mesh_env_key: str, local_env_key: str, default_port: int) -> str:
    explicit_mesh = os.environ.get(mesh_env_key, "").strip()
    if explicit_mesh:
        return explicit_mesh
    local_addr = os.environ.get(local_env_key, "").strip()
    if not local_addr:
        return ""
    from urllib.parse import urlparse
    parsed = urlparse(f"//{local_addr}")
    port = parsed.port or default_port
    try:
        from apps.deployments.services.addon_mesh import _get_master_mesh_ip
        mesh_ip = _get_master_mesh_ip()
    except Exception:
        mesh_ip = "10.100.0.1"
    return f"{mesh_ip}:{port}"


def apply_cryptd_runtime_env(
    service: Any,
    env_vars: dict[str, str],
    *,
    unlock_now: bool = True,
    fail_open: bool | None = None,
) -> bool:
    """
    Register/unlock an opted-in service with `grid-cryptd`, inject
    `GRID_CRYPTD_SERVICE_ID`, and rewrite direct DB/Redis URLs & derived host
    variables through the configured `grid-cryptd` point-of-contact gate.
    """
    if service is None or not cryptd_client.is_enabled() or not is_service_encryption_enabled(service):
        return False

    if fail_open is None:
        fail_open = _parse_bool(os.environ.get("CRYPTD_FAIL_OPEN"), default=False)

    try:
        register_and_unlock_service(service, unlock_now=unlock_now)
        env_vars["GRID_CRYPTD_SERVICE_ID"] = str(service.id)
        if cryptd_client.is_db_gate_enabled():
            pg_gate = os.environ.get("CRYPTD_PG_GATE_ADDR", "").strip()
            redis_gate = os.environ.get("CRYPTD_REDIS_GATE_ADDR", "").strip()
            _rewrite_gate_vars_in_env(env_vars, pg_gate, redis_gate)
        return True
    except Exception as exc:
        if not fail_open:
            raise RuntimeError(
                f"grid-cryptd refused runtime activation for opted-in service {getattr(service, 'name', service)}: {exc}"
            ) from exc
        logger.warning(
            "grid-cryptd runtime activation failed (CRYPTD_FAIL_OPEN=true) for %s: %s",
            getattr(service, "name", service),
            exc,
        )
        return False


def apply_cryptd_remote_runtime_env(
    service: Any,
    env_vars: dict[str, str],
    *,
    unlock_now: bool = True,
    fail_open: bool | None = None,
) -> bool:
    """
    Remote-node counterpart to `apply_cryptd_runtime_env`: enforces lease
    registration/unlock on the Master `grid-cryptd` controller, sets
    `GRID_CRYPTD_SERVICE_ID`, and rewrites DB/Redis gate URLs to the Master's
    WireGuard mesh gate endpoints (`CRYPTD_MESH_*` or MASTER_MESH_IP:<port>).
    """
    if service is None or not cryptd_client.is_enabled() or not is_service_encryption_enabled(service):
        return False

    if fail_open is None:
        fail_open = _parse_bool(os.environ.get("CRYPTD_FAIL_OPEN"), default=False)

    try:
        register_and_unlock_service(service, unlock_now=unlock_now)
        env_vars["GRID_CRYPTD_SERVICE_ID"] = str(service.id)
        if cryptd_client.is_db_gate_enabled():
            pg_gate = _resolve_mesh_gate_addr("CRYPTD_MESH_PG_GATE_ADDR", "CRYPTD_PG_GATE_ADDR", 15432)
            redis_gate = _resolve_mesh_gate_addr("CRYPTD_MESH_REDIS_GATE_ADDR", "CRYPTD_REDIS_GATE_ADDR", 16379)
            _rewrite_gate_vars_in_env(env_vars, pg_gate, redis_gate)
        return True
    except Exception as exc:
        if not fail_open:
            raise RuntimeError(
                f"grid-cryptd refused remote runtime activation for opted-in service {getattr(service, 'name', service)}: {exc}"
            ) from exc
        logger.warning(
            "grid-cryptd remote runtime activation failed (CRYPTD_FAIL_OPEN=true) for %s: %s",
            getattr(service, "name", service),
            exc,
        )
        return False


class CryptdError(RuntimeError):
    """Raised when `grid-cryptd` rejects an operation or is locked."""


class CryptdClient:
    """Thin, stdlib-only HTTP client for `grid-cryptd`."""

    CONTROL_PLANE_SERVICE_ID = "paas-control-plane"
    CONTROL_PLANE_PROJECT_ID = "control-plane"

    def _base_url(self) -> str:
        explicit = (
            os.environ.get("SMSLY_CRYPTD_URL", "").strip()
            or os.environ.get("CRYPTD_URL", "").strip()
        )
        if explicit:
            return explicit.rstrip("/")
        # Inside Docker containers (backend/celery), loopback points to the container
        # itself; reach host systemd grid-cryptd via host.docker.internal.
        if os.path.exists("/.dockerenv"):
            return "http://host.docker.internal:9105"
        return "http://127.0.0.1:9105"

    def _secret(self) -> str:
        return (
            os.environ.get("CRYPTD_SHARED_SECRET", "").strip()
            or os.environ.get("SMSLY_VOLUME_DAEMON_SECRET", "").strip()
        )

    def _timeout(self) -> float:
        try:
            return max(1.0, float(os.environ.get("CRYPTD_HTTP_TIMEOUT", "5.0")))
        except (TypeError, ValueError):
            return 5.0

    def is_enabled(self) -> bool:
        """
        Return True whenever grid-cryptd is configured so per-service opt-ins take
        effect immediately without a global admin toggle, unless explicitly disabled
        via `CRYPTD_ENABLED=false`.
        """
        explicit = os.environ.get("CRYPTD_ENABLED")
        if explicit is not None and str(explicit).strip() != "":
            return _parse_bool(explicit, default=False)

        has_url = bool(
            os.environ.get("SMSLY_CRYPTD_URL", "").strip()
            or os.environ.get("CRYPTD_URL", "").strip()
        )
        return bool(has_url and self._secret())

    def is_db_gate_enabled(self) -> bool:
        return self.is_enabled() and _parse_bool(os.environ.get("CRYPTD_DB_GATE_ENABLED"), default=False)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        require_secret: bool = True,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url()}{path}"
        data = None
        headers = {"Accept": "application/json"}
        secret = self._secret()
        if require_secret and not secret:
            raise CryptdError("CRYPTD_SHARED_SECRET is not configured")
        if secret:
            headers["X-Cryptd-Secret"] = secret

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        req_timeout = max(self._timeout(), float(timeout)) if timeout is not None else self._timeout()
        try:
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw_err = ""
            try:
                raw_err = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise CryptdError(f"cryptd {method} {path} HTTP {exc.code}: {raw_err}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, json.JSONDecodeError) as exc:
            raise CryptdError(f"cryptd {method} {path} failed: {exc}") from exc

    # ── Status & Vault Lifecycle ───────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status")

    def sync_vault(self) -> dict[str, Any]:
        return self._request("POST", "/v1/vault/sync", {}, timeout=30.0)

    def lockdown(self, reason: str = "operator lockdown") -> dict[str, Any]:
        return self._request("POST", "/v1/vault/lockdown", {"reason": reason})

    # ── Service Registration, Opt-In & DB/Redis Gate ───────────────────────────

    def register_service(
        self,
        *,
        service_id: str,
        project_id: str = "",
        opted_in: bool = False,
        db_roles: list[str] | None = None,
        db_names: list[str] | None = None,
        redis_users: list[str] | None = None,
        redis_prefixes: list[str] | None = None,
        upstream_pg: str = "",
        upstream_redis: str = "",
        upstream_tls: bool = False,
        encrypt_redis_vals: bool | None = None,
        unlock_now: bool = False,
        ttl_seconds: int = 7200,
        persistent: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "service_id": str(service_id),
            "project_id": str(project_id or "default"),
            "opted_in": bool(opted_in),
            "db_roles": db_roles or [],
            "db_names": db_names or [],
            "redis_users": redis_users or [],
            "redis_prefixes": redis_prefixes or [],
            "upstream_pg": upstream_pg,
            "upstream_redis": upstream_redis,
            "upstream_tls": bool(upstream_tls),
            "unlock_now": bool(unlock_now and opted_in),
            "ttl_seconds": int(ttl_seconds),
            "persistent": bool(persistent),
        }
        if encrypt_redis_vals is not None:
            body["encrypt_redis_vals"] = bool(encrypt_redis_vals)
        return self._request("POST", "/v1/services/register", body)

    def get_service_policy(self, service_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/services/{service_id}/policy")

    def unlock_service(
        self,
        service_id: str,
        *,
        project_id: str = "",
        ttl_seconds: int = 7200,
        persistent: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/services/{service_id}/unlock",
            {
                "project_id": str(project_id or ""),
                "ttl_seconds": int(ttl_seconds),
                "persistent": bool(persistent),
            },
        )

    def lock_service(self, service_id: str, reason: str = "service stopped or locked") -> dict[str, Any]:
        return self._request("POST", f"/v1/services/{service_id}/lock", {"reason": reason})

    # ── Field / Secret Transit & Dual-Read Compatibility ───────────────────────

    def encrypt_text(
        self,
        plaintext: str,
        *,
        service_id: str,
        project_id: str = "default",
        context: str = "field",
        mode: str = "persistent",
        allow_opt_out: bool = True,
    ) -> str:
        svc_id = str(service_id or "").strip()
        if not svc_id:
            raise CryptdError("service_id is required; grid-cryptd is scoped to services")
        if plaintext is None or plaintext == "":
            return ""
        b64 = base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
        resp = self._request(
            "POST",
            "/v1/transit/encrypt",
            {
                "service_id": svc_id,
                "project_id": str(project_id or "default"),
                "context": context,
                "mode": mode,
                "plaintext_b64": b64,
                "allow_opt_out": bool(allow_opt_out),
            },
        )
        if resp.get("skipped"):
            return plaintext
        tok = resp.get("token")
        if not tok:
            raise CryptdError(f"cryptd transit encrypt missing token: {resp}")
        return str(tok)

    def decrypt_text(
        self,
        token: str,
        *,
        service_id: str,
        project_id: str = "default",
        context: str = "field",
    ) -> str:
        svc_id = str(service_id or "").strip()
        if not svc_id:
            raise CryptdError("service_id is required; grid-cryptd is scoped to services")
        if not token or not isinstance(token, str):
            return token
        if not token.startswith("grid:v1:"):
            return token
        resp = self._request(
            "POST",
            "/v1/transit/decrypt",
            {
                "service_id": svc_id,
                "project_id": str(project_id or "default"),
                "context": context,
                "token": token,
            },
        )
        b64 = resp.get("plaintext_b64", "")
        return base64.b64decode(b64.encode("ascii")).decode("utf-8")

    # ── Backup Envelope Wrapping ───────────────────────────────────────────────

    def wrap_backup_dek(
        self,
        dek_bytes: bytes,
        *,
        service_id: str,
        project_id: str = "default",
    ) -> str:
        svc_id = str(service_id or "").strip()
        if not svc_id:
            raise CryptdError("service_id is required; backup DEK wrapping is scoped to services")
        resp = self._request(
            "POST",
            "/v1/backups/wrap-key",
            {
                "service_id": svc_id,
                "project_id": str(project_id or "default"),
                "dek_b64": base64.b64encode(dek_bytes).decode("ascii"),
            },
        )
        tok = resp.get("wrapped_token")
        if not tok:
            raise CryptdError(f"wrap-key returned empty token: {resp}")
        return str(tok)

    def unwrap_backup_dek(
        self,
        wrapped_token: str,
        *,
        service_id: str,
        project_id: str = "default",
    ) -> bytes:
        svc_id = str(service_id or "").strip()
        if not svc_id:
            raise CryptdError("service_id is required; backup DEK unwrapping is scoped to services")
        resp = self._request(
            "POST",
            "/v1/backups/unwrap-key",
            {
                "service_id": svc_id,
                "project_id": str(project_id or "default"),
                "wrapped_token": wrapped_token,
            },
        )
        return base64.b64decode(resp["dek_b64"].encode("ascii"))

    # ── Encrypted Volumes (zero-key-exposure compatible) ──────────────────────

    def volumes_health(self) -> dict[str, Any]:
        return self._request("GET", "/volumes/health")

    def provision_volume(
        self,
        volume_name: str,
        *,
        service_id: str = "",
        project_id: str = "",
        dek_hex: str = "",
        wrapped_dek: str = "",
        key_id: str = "",
        owner_uid: int = 0,
        owner_gid: int = 0,
        size_mb: int = 0,
        persistent: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "volume_name": volume_name,
            "service_id": str(service_id),
            "project_id": str(project_id),
            "dek_hex": dek_hex,
            "wrapped_dek": wrapped_dek,
            "key_id": key_id,
            "owner_uid": int(owner_uid),
            "owner_gid": int(owner_gid),
        }
        if int(size_mb or 0) > 0:
            payload["size_mb"] = int(size_mb)
        if persistent:
            payload["persistent"] = True
        return self._request(
            "POST",
            "/volumes/provision",
            payload,
            timeout=120.0,
        )

    def unlock_volume(
        self,
        volume_name: str,
        *,
        service_id: str = "",
        project_id: str = "",
        wrapped_dek: str = "",
        dek_hex: str = "",
        key_id: str = "",
        owner_uid: int = 0,
        owner_gid: int = 0,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/volumes/unlock",
            {
                "volume_name": volume_name,
                "service_id": str(service_id),
                "project_id": str(project_id),
                "wrapped_dek": wrapped_dek,
                "dek_hex": dek_hex,
                "key_id": key_id,
                "owner_uid": int(owner_uid),
                "owner_gid": int(owner_gid),
            },
            timeout=60.0,
        )

    def lock_volume(self, volume_name: str, *, service_id: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/volumes/lock",
            {"volume_name": volume_name, "service_id": str(service_id)},
            timeout=60.0,
        )

    def cleanup_volume(self, volume_name: str, *, service_id: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/volumes/cleanup",
            {"volume_name": volume_name, "service_id": str(service_id)},
            timeout=60.0,
        )


cryptd_client = CryptdClient()


def is_service_encryption_enabled(service: Any) -> bool:
    """
    Determine whether encryption-at-rest + DB point-of-contact locking is enabled
    for a specific service. Respects per-service user opt-in so users can choose
    whether to enable it or not.
    """
    if service is None:
        return False
    return bool(getattr(service, "encryption_at_rest_enabled", False))


def _resolve_container_upstream(
    hostname: str,
    port: int | None,
    preferred_network: str = "",
) -> str:
    """Resolve an internal Docker hostname to a host-routable bridge IP:port when possible.

    Scopes lookup to ``preferred_network`` (the target service's project network)
    first before falling back to the shared bridge (``smsly-net``) or any other
    attached network.
    """
    if not hostname or not port:
        return ""
    if hostname in ("127.0.0.1", "localhost", "host.docker.internal"):
        return f"127.0.0.1:{port}"
    try:
        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        if client is not None:
            containers = list(client.containers.list())
            if preferred_network:
                for c in containers:
                    nets = (c.attrs.get("NetworkSettings") or {}).get("Networks") or {}
                    pnet = nets.get(preferred_network)
                    if not isinstance(pnet, dict):
                        continue
                    if c.name == hostname or hostname in (pnet.get("Aliases") or []):
                        ip = pnet.get("IPAddress")
                        if ip:
                            return f"{ip}:{port}"
            for c in containers:
                nets = (c.attrs.get("NetworkSettings") or {}).get("Networks") or {}
                matched = (c.name == hostname)
                if not matched:
                    for net_data in nets.values():
                        if isinstance(net_data, dict) and hostname in (net_data.get("Aliases") or []):
                            matched = True
                            break
                if matched:
                    shared = nets.get("smsly-net")
                    if isinstance(shared, dict) and shared.get("IPAddress"):
                        return f"{shared['IPAddress']}:{port}"
                    for net_data in nets.values():
                        if isinstance(net_data, dict):
                            ip = net_data.get("IPAddress")
                            if ip:
                                return f"{ip}:{port}"
    except Exception:
        pass
    return f"{hostname}:{port}"


def _resolve_upstream_scoped(hostname: str, port: int | None, preferred_network: str = "") -> str:
    if preferred_network:
        try:
            return _resolve_container_upstream(hostname, port, preferred_network=preferred_network)
        except TypeError:
            pass
    return _resolve_container_upstream(hostname, port)


def register_and_unlock_service(service: Any, *, unlock_now: bool = True) -> dict[str, Any] | None:
    """Best-effort sync of a Service's opt-in encryption policy & DB/Redis endpoints with grid-cryptd."""
    if not cryptd_client.is_enabled() or service is None:
        return None
    from urllib.parse import urlparse

    opted = is_service_encryption_enabled(service)
    project_id = str(getattr(service, "project_id", "") or "default")
    svc_id = str(getattr(service, "id", ""))
    if not svc_id:
        return None

    preferred_net = ""
    try:
        project = getattr(service, "project", None)
        if project is not None:
            from apps.deployments.models.network_scope import ScopedNetwork
            preferred_net = ScopedNetwork.resolve_network_name(project) or ""
    except Exception:
        preferred_net = ""

    db_roles: list[str] = []
    db_names: list[str] = []
    redis_users: list[str] = []
    redis_prefixes: list[str] = [f"svc:{svc_id}:"]
    upstream_pg = ""
    upstream_redis = ""
    upstream_redis_tls = False

    slug = getattr(service, "slug", "") or getattr(service, "name", "")
    if slug:
        redis_prefixes.append(f"{slug}:")

    try:
        from apps.deployments.models.addons import Addon
        addons = list(Addon.objects.filter(service=service))
        for addon in addons:
            conn_url = getattr(addon, "connection_url", "") or ""
            if not conn_url:
                continue
            parsed = urlparse(conn_url)
            atype = str(getattr(addon, "addon_type", "") or "").upper()
            if atype in ("POSTGRES", "TIMESCALEDB", "COCKROACHDB"):
                if parsed.username and parsed.username not in db_roles:
                    db_roles.append(parsed.username)
                db_name = (parsed.path or "").lstrip("/")
                if db_name and db_name not in db_names:
                    db_names.append(db_name)
                if not upstream_pg and parsed.hostname:
                    upstream_pg = _resolve_upstream_scoped(parsed.hostname, parsed.port or 5432, preferred_net)
            elif atype in ("REDIS", "VALKEY", "KEYDB", "DRAGONFLYDB"):
                if parsed.username and parsed.username not in redis_users:
                    redis_users.append(parsed.username)
                if not upstream_redis and parsed.hostname:
                    upstream_redis = _resolve_upstream_scoped(parsed.hostname, parsed.port or 6379, preferred_net)
                    if parsed.scheme.lower() == "rediss":
                        upstream_redis_tls = True

        # Shared project Redis addons (e.g. redis-shared owned by another service in the same project)
        if not upstream_redis and getattr(service, "project_id", None):
            proj_redis = list(
                Addon.objects.filter(
                    service__project_id=service.project_id,
                    addon_type__in=("REDIS", "VALKEY", "KEYDB", "DRAGONFLYDB"),
                )
            )
            proj_redis.sort(
                key=lambda a: (
                    0 if ("shared" in str(getattr(a, "name", "") or "").lower()
                          or "redis-shared" in str(getattr(a, "connection_url", "") or "").lower())
                    else 1
                )
            )
            for addon in proj_redis:
                conn_url = getattr(addon, "connection_url", "") or ""
                if not conn_url:
                    continue
                parsed = urlparse(conn_url)
                if parsed.username and parsed.username not in redis_users:
                    redis_users.append(parsed.username)
                if parsed.hostname:
                    upstream_redis = _resolve_upstream_scoped(parsed.hostname, parsed.port or 6379, preferred_net)
                    if upstream_redis:
                        if parsed.scheme.lower() == "rediss":
                            upstream_redis_tls = True
                        break
    except Exception:
        pass

    return cryptd_client.register_service(
        service_id=svc_id,
        project_id=project_id,
        opted_in=opted,
        db_roles=db_roles,
        db_names=db_names,
        redis_users=redis_users,
        redis_prefixes=redis_prefixes,
        upstream_pg=upstream_pg,
        upstream_redis=upstream_redis,
        upstream_tls=upstream_redis_tls,
        encrypt_redis_vals=opted,
        unlock_now=opted and unlock_now,
    )
