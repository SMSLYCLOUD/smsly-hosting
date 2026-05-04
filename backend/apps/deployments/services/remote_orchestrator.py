import logging
import time
import hashlib
import hmac as hmac_mod
import json
import requests
import re
from typing import Optional
from .ssh_client import SSHClient
from apps.deployments.models import (
    Service,
    Deployment,
    ManagedServer,
    EnvironmentVariable,
    PlatformConfig,
)

logger = logging.getLogger(__name__)

class RemoteOrchestrator:
    """
    Handles synchronization and orchestration of services/deployments
    across remote ManagedServer instances.
    """

    def __init__(self, server: ManagedServer):
        self.server = server
        self.base_url = (server.api_url or f"http://{server.host}").rstrip('/')

    def auto_authenticate(self) -> bool:
        """
        Attempt to retrieve API token and Gateway Secret via SSH.
        Returns True if credentials were successfully updated.
        """
        if not self.server.ssh_key and not self.server.ssh_password:
            logger.warning("No SSH credentials for server %s; cannot auto-authenticate.", self.server.host)
            return False

        logger.info("Starting SSH auto-authentication for %s", self.server.host)
        ssh = SSHClient(
            ip=self.server.host,
            key_content=self.server.ssh_key,
            password=self.server.ssh_password,
            user=self.server.ssh_user,
            port=self.server.ssh_port
        )
        try:
            ssh.connect()
            hosting_path = ssh.find_hosting_path()
            
            # 1. Get/Create API Token
            output = ssh.run_diagnose_nodes_fix(hosting_path)
            # Match both smsly_ tokens and standard DRF tokens
            token_match = re.search(r"TOKEN:\s+([a-zA-Z0-9_]+)", output)
            
            updated = False
            if token_match:
                new_token = token_match.group(1)
                if self.server.api_token != new_token:
                    self.server.api_token = new_token
                    updated = True
                    logger.info("Successfully retrieved API token via SSH for %s", self.server.host)
            
            # 2. Get Gateway Secret
            new_secret = ssh.get_gateway_secret(hosting_path)
            if new_secret and self.server.gateway_secret != new_secret:
                self.server.gateway_secret = new_secret
                updated = True
                logger.info("Successfully retrieved Gateway Secret via SSH for %s", self.server.host)

            if updated:
                self.server.save()
                return True
                
        except Exception as e:
            logger.error("SSH auto-authentication failed for %s: %s", self.server.host, e)
        finally:
            ssh.close()
            
        return False

    def _get_headers(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        auth_mode: str | None = None,
    ) -> dict:
        """Build auth headers for the remote server."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-SMSLY-Remote-Sync": "1",
        }
        token = str(self.server.api_token or "").strip()
        gateway_secret = str(self.server.gateway_secret or "").strip()

        if auth_mode in (None, "token") and token:
            if token.lower().startswith(("token ", "bearer ")):
                headers["Authorization"] = token
            elif token.startswith("smsly_"):
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = f"Token {token}"
            return headers

        if auth_mode in (None, "hmac") and gateway_secret:
            timestamp = str(int(time.time()))
            body_hash = hashlib.sha256(body).hexdigest()
            payload = f"{method}|{path}|{timestamp}|{body_hash}"
            signature = hmac_mod.new(
                gateway_secret.encode(),
                payload.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers["X-Gateway-Signature-V2"] = signature
            headers["X-Request-Timestamp"] = timestamp
            return headers

        return headers

    def _auth_modes(self) -> list[str]:
        modes = []
        if str(self.server.api_token or "").strip():
            modes.append("token")
        if str(self.server.gateway_secret or "").strip():
            modes.append("hmac")
        return modes or ["none"]

    @staticmethod
    def _encode_json(payload: dict | None) -> bytes:
        if payload is None:
            return b""
        return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    @staticmethod
    def _path_with_query(path: str, params: dict | None = None) -> str:
        if not params:
            return path
        from urllib.parse import urlencode

        separator = "&" if "?" in path else "?"
        return f"{path}{separator}{urlencode(params, doseq=True)}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
        timeout: int = 15,
        retry_auth: bool = True,
    ) -> requests.Response | None:
        """
        Make a remote API request with token -> HMAC fallback.
        """
        request_path = self._path_with_query(path, params)
        url = f"{self.base_url}{request_path}"
        body = self._encode_json(payload)
        retryable_statuses = {401, 403, 500, 502, 503}
        last_response = None
        modes = self._auth_modes()

        if retry_auth and (not modes or modes == ["none"]):
            # If no auth modes, try auto-auth first
            if self.auto_authenticate():
                modes = self._auth_modes()

        for index, mode in enumerate(modes):
            headers = self._get_headers(
                method,
                path, # Use base path for signing, NOT request_path (no query string)
                body=body,
                auth_mode=None if mode == "none" else mode,
            )
            try:
                response = requests.request(
                    method,
                    url,
                    data=body if payload is not None else None,
                    headers=headers,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Remote request failed on %s %s via %s auth: %s",
                    method,
                    request_path,
                    mode,
                    exc,
                )
                continue

            last_response = response
            has_more_modes = index < len(modes) - 1
            if has_more_modes and response.status_code in retryable_statuses:
                logger.warning(
                    "Remote request %s %s returned HTTP %s via %s auth; trying next auth mode.",
                    method,
                    request_path,
                    response.status_code,
                    mode,
                )
                continue

            if retry_auth and response.status_code in (401, 403) and not has_more_modes:
                # If we failed all modes with 401/403, try one last auto-auth retry
                if self.auto_authenticate():
                    return self._request(
                        method, path, payload=payload, params=params, 
                        timeout=timeout, retry_auth=False
                    )

            return response

        return last_response

    def sync_service(self, service: Service) -> Optional[str]:
        """
        Ensure the service exists on the remote server.
        Returns the remote service ID (UUID string) on success.
        """
        path = "/api/v1/services/"
        
        # 1. Search for service by name on remote
        try:
            resp = self._request("GET", path, params={"search": service.name}, timeout=15)
            if resp and resp.status_code == 200:
                results = resp.json()
                if isinstance(results, dict):
                    results = results.get("results", [])
                
                for remote_svc in results:
                    if remote_svc["name"] == service.name:
                        logger.info("Found existing service %s on remote %s", service.name, self.server.host)
                        return remote_svc["id"]
        except Exception as e:
            logger.warning("Failed to search service on remote %s: %s", self.server.host, e)

        # 2. Not found -> Create it
        logger.info("Creating service %s on remote %s", service.name, self.server.host)
        payload = {
            "name": service.name,
            "deploy_type": service.deploy_type,
            "repository_url": service.repository_url,
            "branch": service.branch,
            "docker_image": service.docker_image,
            "internal_port": service.internal_port,
            "is_public": service.is_public,
            "buildpack": service.buildpack,
        }
        
        try:
            resp = self._request("POST", path, payload=payload, timeout=15)
            if resp and resp.status_code in (201, 200):
                remote_id = resp.json()["id"]
                
                # Sync environment variables
                self.sync_env_vars(service, remote_id)
                return remote_id
            
            logger.error(
                "Failed to create service on remote: %s",
                resp.text if resp is not None else "no response",
            )
        except Exception as e:
            logger.error("Error creating service on remote: %s", e)
            
        return None

    def sync_env_vars(self, service: Service, remote_service_id: str):
        """Sync environment variables to the remote service."""
        path = f"/api/v1/services/{remote_service_id}/env_vars/"
        env_vars = EnvironmentVariable.objects.filter(service=service)
        
        for var in env_vars:
            payload = {
                "key": var.key,
                "value": var.value,
                "is_secret": var.is_secret,
                "source": var.source,
            }
            try:
                self._request("POST", path, payload=payload, timeout=10)
            except Exception:
                pass

    def trigger_deploy(self, deployment: Deployment, remote_service_id: str) -> Optional[str]:
        """
        Trigger a deployment on the remote server for the given service.
        Returns the remote deployment ID on success.
        """
        path = f"/api/v1/services/{remote_service_id}/deploy/"
        ref = deployment.commit_hash or "HEAD"
        if ref == "latest":
            ref = "HEAD"

        config = PlatformConfig.load()
        payload = {
            "ref": ref,
            "source_node": config.server_ip or "controller",
        }
        
        try:
            resp = self._request("POST", path, payload=payload, timeout=15)
            if resp and resp.status_code in (201, 200, 202):
                return resp.json().get("deployment_id") or resp.json().get("id")
            logger.error(
                "Failed to trigger remote deploy: %s",
                resp.text if resp is not None else "no response",
            )
        except Exception as e:
            logger.error("Error triggering remote deploy: %s", e)
            
        return None

    def approve_deployment(self, remote_deployment_id: str, payload: dict | None = None) -> bool:
        """Approve a paused remote REVIEW deployment."""
        path = f"/api/v1/deployments/{remote_deployment_id}/approve/"

        try:
            resp = self._request("POST", path, payload=payload or {}, timeout=15)
            if resp and resp.status_code in (200, 202):
                return True
            logger.error(
                "Failed to approve remote deploy: %s",
                resp.text if resp is not None else "no response",
            )
        except Exception as e:
            logger.error("Error approving remote deploy: %s", e)

        return False

    def poll_deployment(self, remote_deployment_id: str) -> dict:
        """Fetch the current status and logs of a remote deployment."""
        path = f"/api/v1/deployments/{remote_deployment_id}/"
        
        try:
            resp = self._request("GET", path, timeout=10)
            if resp and resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
            
        return {}

    def delete_service(self, remote_service_id: str) -> bool:
        """Tell the remote server to delete the given service."""
        path = f"/api/v1/services/{remote_service_id}/"
        
        try:
            resp = self._request("DELETE", path, timeout=20)
            # 202 Accepted, 204 No Content, 200 OK, or 404 Not Found (already gone)
            return bool(resp and resp.status_code in (202, 204, 200, 404))
        except Exception as e:
            logger.error("Error deleting service on remote: %s", e)
            return False
