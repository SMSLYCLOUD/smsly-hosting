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
    "http://infisical:8080",
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
        self.token = token or os.environ.get("INFISICAL_SERVICE_TOKEN", "")
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


def get_infisical_client() -> InfisicalClient | None:
    """Return an Infisical client or None if not configured."""
    token = os.environ.get("INFISICAL_SERVICE_TOKEN", "")
    base_url = INFISICAL_API_URL
    if not base_url:
        return None
    return InfisicalClient(base_url=base_url, token=token)


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
    from apps.deployments.models_core import PlatformConfig

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
    from apps.deployments.models_core import PlatformConfig

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
    if os.environ.get("INFISICAL_SERVICE_TOKEN"):
        env["INFISICAL_TOKEN"] = os.environ["INFISICAL_SERVICE_TOKEN"]
    if INFISICAL_API_URL:
        env["INFISICAL_API_URL"] = INFISICAL_API_URL
    if workspace_id:
        env["INFISICAL_WORKSPACE_ID"] = workspace_id
    env["INFISICAL_ENVIRONMENT"] = "prod"
    env["INFISICAL_SERVICE_ID"] = service_id
    return env
