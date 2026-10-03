"""
Infisical integration — bridges PlatformConfig with Infisical's secret management.

Provides:
  - push_secrets_to_infisical(): Sync PlatformConfig secrets to Infisical
  - pull_secrets_from_infisical(): Pull secrets from Infisical into PlatformConfig
  - infisical_client(): Return a configured Infisical API client
"""

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

INFISICAL_URL = os.environ.get(
    "INFISICAL_URL",
    "https://infisical:8443",
)
INFISICAL_API_URL = f"{INFISICAL_URL}/api/v1"

_site_url = os.environ.get("INFISICAL_SITE_URL", "")
_site_public_url = None
if _site_url:
    _site_public_url = _site_url
    INFISICAL_API_URL = f"{_site_url}/api/v1"
elif not INFISICAL_URL.startswith("http"):
    INFISICAL_API_URL = "http://infisical:8080/api/v1"


class InfisicalClient:
    """Minimal Infisical API client for secret operations."""

    def __init__(self, base_url: str = INFISICAL_API_URL, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        resolved = token if token else resolve_service_token()
        self.token = resolved
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "smsly-platform/1.0",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, timeout=15, **kwargs)
        if resp.status_code >= 400:
            logger.warning("Infisical API %s %s → %s: %s", method, path, resp.status_code, resp.text[:300])
        return resp

    def get_workspaces(self) -> list[dict]:
        resp = self._request("GET", "/workspace")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("workspaces", [])
        return []

    def get_secrets(self, workspace_id: str, environment: str = "prod", _path: str = "/") -> list[dict]:
        resp = self._request(
            "GET",
            f"/secret/{workspace_id}",
            params={"environment": environment, "workspaceId": workspace_id},
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("secrets", [])
        return []

    def create_secret(
        self,
        workspace_id: str,
        secret_name: str,
        secret_value: str,
        environment: str = "prod",
        _path: str = "/",
        secret_type: str = "shared",
    ) -> bool:
        resp = self._request(
            "POST",
            f"/secret/{secret_name}",
            json={
                "workspaceId": workspace_id,
                "environment": environment,
                "type": secret_type,
                "secretKey": secret_name,
                "secretValue": secret_value,
                "secretPath": _path,
            },
        )
        return resp.status_code in (200, 201)

    def update_secret(
        self,
        workspace_id: str,
        secret_name: str,
        secret_value: str,
        environment: str = "prod",
        _path: str = "/",
    ) -> bool:
        resp = self._request(
            "PATCH",
            f"/secret/{secret_name}",
            json={
                "workspaceId": workspace_id,
                "environment": environment,
                "type": "shared",
                "secretKey": secret_name,
                "secretValue": secret_value,
                "secretPath": _path,
            },
        )
        return resp.status_code in (200, 201)

    def delete_secret(self, workspace_id: str, secret_name: str, environment: str = "prod", _path: str = "/") -> bool:
        resp = self._request(
            "DELETE",
            f"/secret/{secret_name}",
            json={
                "workspaceId": workspace_id,
                "environment": environment,
                "type": "shared",
                "secretPath": _path,
            },
        )
        return resp.status_code in (200, 204)


INFISICAL_MESH_FORWARD_PORT = 25010
INFISICAL_MESH_FORWARDER_NAME = "smsly-mesh-fwd-infisical"


def resolve_service_token() -> str:
    """DB first, env fallback. DB wins so rotation needs no restart."""
    try:
        from apps.deployments.models.core import PlatformConfig
        token = str(PlatformConfig.load().infisical_service_token or "").strip()
        if token:
            return token
    except Exception:
        pass
    return os.environ.get("INFISICAL_SERVICE_TOKEN", "").strip()


def resolve_api_url(for_remote: bool = False) -> str:
    """Base API URL (no trailing /api/v1).

    Local: Docker DNS as before. Remote (node) callers must use the
    WireGuard-mesh forwarder (see ensure_infisical_mesh_forwarder):
    nodes have no route to master's docker DNS and must not depend on
    public DNS for vault traffic.
    """
    if for_remote:
        try:
            from apps.deployments.services.addon_mesh import _get_master_mesh_ip
            mesh_ip = _get_master_mesh_ip()
        except Exception:
            mesh_ip = "10.100.0.1"
        return f"http://{mesh_ip}:{INFISICAL_MESH_FORWARD_PORT}"
    return INFISICAL_URL


def ensure_infisical_mesh_forwarder() -> str:
    """Expose Infisical on the master mesh IP via a socat forwarder.

    Mirrors addon_mesh forwarders (bound strictly to the mesh IP, never
    0.0.0.0). Returns the remote base URL, or '' when it cannot be built.
    Fail-open: callers fall back to existing behavior.
    """
    import subprocess

    try:
        from apps.deployments.services.addon_mesh import _get_master_mesh_ip
        mesh_ip = _get_master_mesh_ip()
    except Exception:
        mesh_ip = "10.100.0.1"
    base_url = f"http://{mesh_ip}:{INFISICAL_MESH_FORWARD_PORT}"
    chk = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", INFISICAL_MESH_FORWARDER_NAME],
        capture_output=True, text=True, timeout=30,
    )
    if chk.returncode == 0 and chk.stdout.strip() == "true":
        return base_url
    subprocess.run(["docker", "rm", "-f", INFISICAL_MESH_FORWARDER_NAME],
                   capture_output=True, timeout=60)
    res = subprocess.run(
        ["docker", "run", "-d",
         "--name", INFISICAL_MESH_FORWARDER_NAME,
         "--restart", "unless-stopped",
         "--network", "smsly-net",
         "-p", f"{mesh_ip}:{INFISICAL_MESH_FORWARD_PORT}:8080",
         "alpine/socat:latest",
         "tcp-listen:8080,fork,reuseaddr",
         "tcp-connect:infisical:8080"],
        capture_output=True, text=True, timeout=120,
    )
    if res.returncode != 0:
        logger.warning("Infisical mesh forwarder failed: %s", (res.stderr or "")[-200:])
        return ""
    return base_url


def get_infisical_client(for_remote: bool = False) -> InfisicalClient | None:
    """Return an Infisical client or None if not configured.

    Token: PlatformConfig DB first, INFISICAL_SERVICE_TOKEN env fallback
    (no backend restart needed after rotation). URL: remote callers use
    the mesh forwarder, local callers keep Docker DNS behavior.

    Cooperative wake: local callers ensure the `secrets` tier is awake
    via napd first (warn-only — a sleeping Infisical surfaces as the
    usual connection error, not a new failure mode).
    """
    if not for_remote:
        try:
            from apps.deployments.services.tiers import ensure_tier_awake
            ensure_tier_awake("secrets", timeout=120)
        except Exception:
            pass
    token = resolve_service_token()
    if for_remote:
        base_url = f"{resolve_api_url(for_remote=True)}/api/v1"
    else:
        base_url = INFISICAL_API_URL
    if not base_url:
        return None
    return InfisicalClient(base_url=base_url, token=token)
    return base_url


def get_or_create_workspace(client: InfisicalClient, workspace_name: str = "smsly-platform") -> str | None:
    """Get or create an Infisical workspace. Returns workspace_id."""
    workspaces = client.get_workspaces()
    for ws in workspaces:
        if ws.get("name") == workspace_name:
            return ws["id"]
    resp = client._request(
        "POST",
        "/workspace",
        json={"workspaceName": workspace_name, "organizationId": ""},
    )
    if resp.status_code == 200:
        return resp.json().get("workspace", {}).get("id")
    return None


def push_platform_config_to_infisical(
    client: InfisicalClient | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """
    Sync PlatformConfig secrets to Infisical.

    Pushes encrypted platform secrets to Infisical so user-deployed
    containers can reference them via Infisical SDK or env injection.
    """
    from apps.deployments.models.core import PlatformConfig

    if client is None:
        client = get_infisical_client()
    if client is None:
        return {"ok": False, "error": "Infisical not configured"}

    if workspace_id is None:
        workspace_id = get_or_create_workspace(client)
    if workspace_id is None:
        return {"ok": False, "error": "Could not resolve Infisical workspace"}

    config = PlatformConfig.load()

    # Secrets to sync (field_name → nice_name in Infisical)
    secrets_to_sync: dict[str, str] = {
        "container_registry_url": "SMSLY_REGISTRY_URL",
        "registry_user": "SMSLY_REGISTRY_USER",
        "registry_password": "SMSLY_REGISTRY_PASSWORD",
        "cloudflare_api_token": "CLOUDFLARE_API_TOKEN",
        "smtp_host": "SMSLY_SMTP_HOST",
        "smtp_port": "SMSLY_SMTP_PORT",
        "smtp_user": "SMSLY_SMTP_USER",
        "smtp_password": "SMSLY_SMTP_PASSWORD",
        "github_app_id": "SMSLY_GITHUB_APP_ID",
        "github_app_private_key": "SMSLY_GITHUB_APP_PRIVATE_KEY",
        "github_webhook_secret": "SMSLY_GITHUB_WEBHOOK_SECRET",
        "gateway_secret": "SMSLY_GATEWAY_SECRET",
        "crowdsec_bouncer_key": "SMSLY_CROWDSEC_BOUNCER_KEY",
    }

    results = {"synced": [], "failed": [], "skipped": []}

    for field_name, infisical_name in secrets_to_sync.items():
        value = getattr(config, field_name, None)
        if value is None or str(value).strip() == "":
            results["skipped"].append(infisical_name)
            continue

        value_str = str(value)
        existing = client.get_secrets(workspace_id)
        existing_names = {s.get("secretKey") for s in existing}

        try:
            if infisical_name in existing_names:
                ok = client.update_secret(workspace_id, infisical_name, value_str)
            else:
                ok = client.create_secret(workspace_id, infisical_name, value_str)
            if ok:
                results["synced"].append(infisical_name)
            else:
                results["failed"].append(infisical_name)
        except Exception as exc:
            logger.error("Infisical push failed for %s: %s", infisical_name, exc)
            results["failed"].append(infisical_name)

    results["ok"] = len(results["failed"]) == 0
    logger.info(
        "Infisical push: synced=%d, failed=%d, skipped=%d",
        len(results["synced"]),
        len(results["failed"]),
        len(results["skipped"]),
    )
    return results


def pull_platform_config_from_infisical(
    client: InfisicalClient | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """
    Pull secrets from Infisical into PlatformConfig.

    Use when secrets are managed in Infisical and need to be synced
    back to the platform DB.
    """
    from apps.deployments.models.core import PlatformConfig

    if client is None:
        client = get_infisical_client()
    if client is None:
        return {"ok": False, "error": "Infisical not configured"}

    if workspace_id is None:
        workspace_id = get_or_create_workspace(client)
    if workspace_id is None:
        return {"ok": False, "error": "Could not resolve Infisical workspace"}

    config = PlatformConfig.load()
    secrets = client.get_secrets(workspace_id)
    secret_map = {s.get("secretKey"): s.get("secretValue") for s in secrets}

    infisical_to_field: dict[str, str] = {
        "SMSLY_REGISTRY_URL": "container_registry_url",
        "SMSLY_REGISTRY_USER": "registry_user",
        "SMSLY_REGISTRY_PASSWORD": "registry_password",
        "CLOUDFLARE_API_TOKEN": "cloudflare_api_token",
        "SMSLY_SMTP_HOST": "smtp_host",
        "SMSLY_SMTP_PORT": "smtp_port",
        "SMSLY_SMTP_USER": "smtp_user",
        "SMSLY_SMTP_PASSWORD": "smtp_password",
        "SMSLY_GITHUB_APP_ID": "github_app_id",
        "SMSLY_GITHUB_APP_PRIVATE_KEY": "github_app_private_key",
        "SMSLY_GITHUB_WEBHOOK_SECRET": "github_webhook_secret",
        "SMSLY_GATEWAY_SECRET": "gateway_secret",
        "SMSLY_CROWDSEC_BOUNCER_KEY": "crowdsec_bouncer_key",
    }

    results = {"updated": [], "skipped": []}

    for infisical_name, field_name in infisical_to_field.items():
        value = secret_map.get(infisical_name)
        if value is None:
            results["skipped"].append(field_name)
            continue

        try:
            setattr(config, field_name, str(value))
            results["updated"].append(field_name)
        except Exception as exc:
            logger.error("Infisical pull failed for %s: %s", field_name, exc)

    if results["updated"]:
        config.save()

    results["ok"] = True
    logger.info(
        "Infisical pull: updated=%d, skipped=%d",
        len(results["updated"]),
        len(results["skipped"]),
    )
    return results


def inject_infisical_env_for_service(
    service_id: str,
    client: InfisicalClient | None = None,
    workspace_id: str | None = None,
) -> dict[str, str]:
    """
    Generate env vars for a deployed service that pulls from Infisical.

    Returns a dict of INFISICAL_* env vars to inject into the container.
    The container can then use the Infisical SDK or agent to pull secrets
    at startup.
    """
    env: dict[str, str] = {}
    token = resolve_service_token()
    if token:
        env["INFISICAL_TOKEN"] = token
    if INFISICAL_API_URL:
        env["INFISICAL_API_URL"] = INFISICAL_API_URL
    if workspace_id:
        env["INFISICAL_WORKSPACE_ID"] = workspace_id
    env["INFISICAL_ENVIRONMENT"] = "prod"
    env["INFISICAL_SERVICE_ID"] = service_id
    return env


def push_service_secrets_to_infisical(
    service_id: str,
    client: InfisicalClient | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Push a service's secret env vars to Infisical.

    Creates/updates secrets under path /service/<service_id>/ so they are
    isolated per-service. Call before build/deploy so the runtime can pull
    via the Infisical SDK instead of plaintext env.
    """
    from apps.deployments.models.core import EnvironmentVariable

    if client is None:
        client = get_infisical_client()
    if client is None:
        return {"ok": False, "error": "Infisical not configured"}
    if workspace_id is None:
        workspace_id = get_or_create_workspace(client)
    if workspace_id is None:
        return {"ok": False, "error": "No workspace"}

    env_vars = EnvironmentVariable.objects.filter(service_id=service_id, is_secret=True)
    results: dict[str, Any] = {"synced": [], "failed": [], "skipped": []}
    for ev in env_vars:
        if not ev.key or not ev.value:
            results["skipped"].append(ev.key)
            continue
        path = f"/service/{service_id}"
        try:
            existing = client.get_secrets(workspace_id, _path=path)
            names = {s.get("secretKey") for s in existing}
            ok = client.update_secret(workspace_id, ev.key, ev.value, _path=path) if ev.key in names else client.create_secret(workspace_id, ev.key, ev.value, _path=path)
            (results["synced"] if ok else results["failed"]).append(ev.key)
        except Exception as exc:
            logger.warning("Infisical push failed for %s/%s: %s", service_id, ev.key, exc)
            results["failed"].append(ev.key)
    results["ok"] = len(results["failed"]) == 0
    logger.info("Infisical service push %s: synced=%d failed=%d", service_id, len(results["synced"]), len(results["failed"]))
    return results


def ensure_cached(max_age_s: int = 3600) -> dict:
    """Cached best-effort ensure for hot paths (deploy pipeline).

    Validates at most once per hour; a broken token triggers the full
    ensure (mint/rotate) immediately. Never raises.
    """
    from django.core.cache import cache as _cache

    try:
        stamp = _cache.get("smsly:infisical:ensure:v1")
    except Exception:
        stamp = None
    import time as _time
    now = _time.time()
    if stamp:
        try:
            if now - float(stamp) < max_age_s:
                return {"ok": True, "rotated": False, "reason": "recently verified"}
        except Exception:
            pass
    try:
        result = ensure_infisical_service_token()
    except Exception as exc:
        return {"ok": False, "rotated": False, "reason": f"ensure crashed: {exc}"}
    if result.get("ok"):
        try:
            _cache.set("smsly:infisical:ensure:v1", now, max_age_s)
        except Exception:
            pass
    return result


def is_infisical_healthy(client: InfisicalClient | None = None) -> bool:
    """Check if Infisical is reachable and authenticated.

    A 401 used to count as healthy (get_workspaces returns [] on any
    non-200) — wrong: callers then pushed per-secret into auth
    failures. Only a real workspace list counts now.
    """
    return check_infisical_auth(client) is True


def check_infisical_auth(client: InfisicalClient | None = None) -> bool | None:
    """True = token works, False = rejected (401/403), None = transport/other error."""
    if client is None:
        client = get_infisical_client()
    if client is None:
        return None
    if not client.token:
        return False
    try:
        resp = client.session.request("GET", f"{client.base_url}/workspace", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return isinstance(data.get("workspaces"), list)
        if resp.status_code in (401, 403):
            return False
        return None
    except Exception:
        return None


def _read_admin_bootstrap() -> tuple[str, str]:
    """Admin email/password from the provision-time bootstrap file."""
    for candidate in (
        "/opt/smsly-hosting/.infisical-admin",
        os.path.join(os.environ.get("INSTALL_DIR", "/opt/smsly-hosting"), ".infisical-admin"),
    ):
        try:
            with open(candidate) as fh:
                vals = dict(
                    line.strip().split("=", 1)
                    for line in fh
                    if "=" in line and not line.strip().startswith("#")
                )
            email = (vals.get("ADMIN_EMAIL") or "").strip()
            password = (vals.get("ADMIN_PASSWORD") or "").strip()
            if email and password:
                return email, password
        except Exception:
            continue
    return "", ""


def _try_mint_service_token(base_url: str, email: str, password: str) -> str:
    """Mint a service token via the admin account; return token or ''.

    Tries known legacy API shapes and verifies each candidate by
    listing workspaces with it. Loud redacted logs on every miss —
    version drift must be visible, never silent.
    """
    import json as _json

    def _login() -> str:
        resp = requests.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": email, "password": password},
            timeout=20,
        )
        if resp.status_code not in (200, 201):
            logger.warning("Infisical admin login → %s", resp.status_code)
            return ""
        try:
            data = resp.json()
        except Exception:
            return ""
        for key in ("token", "accessToken", "access_token"):
            val = data.get(key)
            if isinstance(val, str) and len(val) > 20:
                return val
        user = data.get("user") or {}
        for key in ("token", "accessToken"):
            val = user.get(key)
            if isinstance(val, str) and len(val) > 20:
                return val
        return ""

    def _verify(token: str) -> bool:
        try:
            resp = requests.get(
                f"{base_url}/api/v1/workspace",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            return resp.status_code == 200 and isinstance(resp.json().get("workspaces"), list)
        except Exception:
            return False

    jwt = _login()
    if not jwt:
        return ""
    headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
    workspaces: list[dict] = []
    try:
        resp = requests.get(f"{base_url}/api/v1/workspace", headers=headers, timeout=15)
        if resp.status_code == 200:
            workspaces = resp.json().get("workspaces", []) or []
    except Exception as exc:
        logger.debug("Infisical workspace list for mint failed: %s", exc)
    ws_id = workspaces[0].get("id") if workspaces else ""
    attempts = [
        ("POST", "/api/v1/service-token",
         {"name": "smsly-platform", "workspaceId": ws_id, "permissions": ["read", "write"]}),
        ("POST", "/api/v2/service-token",
         {"name": "smsly-platform", "workspaceId": ws_id}),
        ("POST", f"/api/v1/workspace/{ws_id}/service-token" if ws_id else "/api/v1/service-token",
         {"name": "smsly-platform"}),
    ]
    for method, path, payload in attempts:
        try:
            resp = requests.request(method, f"{base_url}{path}", headers=headers,
                                    data=_json.dumps({k: v for k, v in payload.items() if v}),
                                    timeout=20)
            if resp.status_code not in (200, 201):
                logger.warning("Infisical mint %s %s → %s", method, path, resp.status_code)
                continue
            try:
                data = resp.json()
            except Exception:
                continue
            for key in ("token", "serviceToken", "service_token"):
                cand = data.get(key)
                if isinstance(cand, str) and len(cand) > 20 and _verify(cand):
                    return cand
                nested = data.get("serviceToken") or {}
                if isinstance(nested, dict):
                    cand = nested.get("token", "")
                    if isinstance(cand, str) and len(cand) > 20 and _verify(cand):
                        return cand
            logger.warning("Infisical mint %s %s: no verifiable token in response", method, path)
        except Exception as exc:
            logger.debug("Infisical mint attempt failed: %s", exc)
    return ""


def ensure_infisical_service_token() -> dict:
    """Validate the vault token; mint + store when broken and possible.

    Never raises. Returns {ok, rotated, reason}. Rotation writes the
    DB field (live immediately, no restart) and never touches .env.
    Auto-mint needs the provision-time admin bootstrap file; without it
    the result explains the exact manual step instead of failing silently.
    """
    client = get_infisical_client()
    if client is None:
        return {"ok": False, "rotated": False, "reason": "no API URL configured"}
    if check_infisical_auth(client) is True:
        return {"ok": True, "rotated": False, "reason": "token valid"}
    if client.token:
        logger.warning("Infisical token rejected (401/403) — attempting rotation")
    else:
        logger.warning("Infisical service token missing — attempting auto-mint")
    email, password = _read_admin_bootstrap()
    if not email or not password:
        return {
            "ok": False, "rotated": False,
            "reason": "no working token and admin bootstrap file unreadable — mint at secrets UI (Organization Settings → Service Tokens) and save to PlatformConfig infisical_service_token",
        }
    base_url = INFISICAL_API_URL or "http://infisical:8080/api/v1"
    minted = _try_mint_service_token(base_url, email, password)
    if not minted:
        return {"ok": False, "rotated": False, "reason": "auto-mint failed — see warnings above; mint manually"}
    try:
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        cfg.infisical_service_token = minted
        cfg.save(update_fields=["infisical_service_token"])
    except Exception as exc:
        return {"ok": False, "rotated": False, "reason": f"minted but DB store failed: {exc}"}
    logger.info("Infisical service token rotated and stored (DB, live immediately)")
    return {"ok": True, "rotated": True, "reason": "minted via admin bootstrap"}
