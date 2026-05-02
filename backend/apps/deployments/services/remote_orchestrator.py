import logging
import time
import hashlib
import hmac as hmac_mod
import requests
from typing import Any, Optional, Tuple
from django.utils import timezone
from django.conf import settings
from apps.deployments.models import Service, Deployment, ManagedServer, EnvironmentVariable

logger = logging.getLogger(__name__)

class RemoteOrchestrator:
    """
    Handles synchronization and orchestration of services/deployments
    across remote ManagedServer instances.
    """

    def __init__(self, server: ManagedServer):
        self.server = server
        self.base_url = server.api_url.rstrip('/')

    def _get_headers(self, method: str, path: str, body: bytes = b"") -> dict:
        """Build auth headers for the remote server."""
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        token = str(self.server.api_token or "").strip()
        gateway_secret = str(self.server.gateway_secret or "").strip()

        # Try Token Auth first
        if token:
            if token.lower().startswith(("token ", "bearer ")):
                headers["Authorization"] = token
            elif token.startswith("smsly_"):
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = f"Token {token}"
            return headers

        # Fallback to HMAC V2
        if gateway_secret:
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

    def sync_service(self, service: Service) -> Optional[str]:
        """
        Ensure the service exists on the remote server.
        Returns the remote service ID (UUID string) on success.
        """
        path = "/api/v1/services/"
        
        # 1. Search for service by name on remote
        headers = self._get_headers("GET", path)
        try:
            resp = requests.get(f"{self.base_url}{path}", params={"search": service.name}, headers=headers, timeout=15)
            if resp.status_code == 200:
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
        
        body = requests.models.complexjson.dumps(payload).encode()
        headers = self._get_headers("POST", path, body=body)
        
        try:
            resp = requests.post(f"{self.base_url}{path}", json=payload, headers=headers, timeout=15)
            if resp.status_code in (201, 200):
                remote_id = resp.json()["id"]
                
                # Sync environment variables
                self.sync_env_vars(service, remote_id)
                return remote_id
            
            logger.error("Failed to create service on remote: %s", resp.text)
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
            body = requests.models.complexjson.dumps(payload).encode()
            headers = self._get_headers("POST", path, body=body)
            try:
                requests.post(f"{self.base_url}{path}", json=payload, headers=headers, timeout=10)
            except Exception:
                pass

    def trigger_deploy(self, deployment: Deployment, remote_service_id: str) -> Optional[str]:
        """
        Trigger a deployment on the remote server for the given service.
        Returns the remote deployment ID on success.
        """
        path = f"/api/v1/services/{remote_service_id}/deploy/"
        payload = {
            "commit_hash": deployment.commit_hash,
            "commit_message": deployment.commit_message,
            "is_rollback": deployment.is_rollback,
        }
        
        body = requests.models.complexjson.dumps(payload).encode()
        headers = self._get_headers("POST", path, body=body)
        
        try:
            resp = requests.post(f"{self.base_url}{path}", json=payload, headers=headers, timeout=15)
            if resp.status_code in (201, 200, 202):
                return resp.json().get("deployment_id") or resp.json().get("id")
            logger.error("Failed to trigger remote deploy: %s", resp.text)
        except Exception as e:
            logger.error("Error triggering remote deploy: %s", e)
            
        return None

    def poll_deployment(self, remote_deployment_id: str) -> dict:
        """Fetch the current status and logs of a remote deployment."""
        path = f"/api/v1/deployments/{remote_deployment_id}/"
        headers = self._get_headers("GET", path)
        
        try:
            resp = requests.get(f"{self.base_url}{path}", headers=headers, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
            
        return {}

    def delete_service(self, remote_service_id: str) -> bool:
        """Tell the remote server to delete the given service."""
        path = f"/api/v1/services/{remote_service_id}/"
        headers = self._get_headers("DELETE", path)
        
        try:
            resp = requests.delete(f"{self.base_url}{path}", headers=headers, timeout=20)
            # 202 Accepted or 204 No Content
            return resp.status_code in (202, 204, 200)
        except Exception as e:
            logger.error("Error deleting service on remote: %s", e)
            return False
