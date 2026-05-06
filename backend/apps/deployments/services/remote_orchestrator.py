import logging
import time
import hashlib
import hmac as hmac_mod
import json
import requests
import re
from typing import Optional
from urllib.parse import urlencode, urlparse
from .ssh_client import SSHClient
from apps.deployments.models import (
    Service,
    Deployment,
    ManagedServer,
    EnvironmentVariable,
    PlatformConfig,
)

logger = logging.getLogger(__name__)


REMOTE_RESPONSE_SNIPPET_CHARS = 1200
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "DELETE"}


def _safe_error_snippet(value: object, limit: int = REMOTE_RESPONSE_SNIPPET_CHARS) -> str:
    """Return a short, NUL-free, best-effort redacted error snippet."""
    text = str(value or "").replace("\x00", "")
    text = re.sub(
        r"(?i)((?:authorization|api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;}{]+",
        r"\1***",
        text,
    )
    return text[:limit]


class RemoteOrchestrator:
    """
    Handles synchronization and orchestration of services/deployments
    across remote ManagedServer instances.
    """

    def __init__(self, server: ManagedServer):
        self.server = server
        self.base_url = (server.api_url or f"http://{server.host}").rstrip('/')
        self.last_error = ""

    def _set_last_error(self, message: str, response: requests.Response | None = None):
        detail = _safe_error_snippet(message)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            text = _safe_error_snippet(getattr(response, "text", ""))
            if text:
                detail = f"{detail} Response: {text}"
            if status_code and f"HTTP {status_code}" not in detail:
                detail = f"HTTP {status_code}: {detail}"
        self.last_error = detail

    def describe_last_error(self) -> str:
        """Human-readable reason for the most recent remote request failure."""
        return self.last_error

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

        separator = "&" if "?" in path else "?"
        return f"{path}{separator}{urlencode(params, doseq=True)}"

    def _candidate_base_urls(self) -> list[str]:
        """Return API base URLs worth trying without mutating the saved server."""
        urls: list[str] = []

        def append(value: str):
            normalized = str(value or "").strip().rstrip("/")
            if normalized and normalized not in urls:
                urls.append(normalized)

        append(self.base_url)

        host_port = str(getattr(self.server, "host", "") or "").strip().rstrip("/")
        if "://" in host_port:
            parsed = urlparse(host_port)
            host_port = parsed.netloc or parsed.path
        host_port = host_port.split("/", 1)[0].strip()
        if not host_port:
            return urls

        has_explicit_port = host_port.count(":") == 1
        append(f"http://{host_port}")
        append(f"https://{host_port}")
        if not has_explicit_port:
            append(f"http://{host_port}:8090")
        return urls

    @staticmethod
    def _timeout(timeout: int | float | tuple | None):
        if timeout is None:
            return (5, 20)
        if isinstance(timeout, tuple):
            return timeout
        return (5, timeout)

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
        self.last_error = ""
        request_path = self._path_with_query(path, params)
        body = self._encode_json(payload)
        method_upper = method.upper()
        auth_retry_statuses = {401, 403}
        network_retry_statuses = {429, 500, 502, 503, 504}
        last_response = None
        modes = self._auth_modes()

        if retry_auth and (not modes or modes == ["none"]):
            # If no auth modes, try auto-auth first
            if self.auto_authenticate():
                modes = self._auth_modes()

        for base_url in self._candidate_base_urls():
            url = f"{base_url}{request_path}"
            redirected = False

            for index, mode in enumerate(modes):
                headers = self._get_headers(
                    method_upper,
                    path,  # Use base path for signing, NOT request_path.
                    body=body,
                    auth_mode=None if mode == "none" else mode,
                )
                attempts = 2 if method_upper in SAFE_METHODS else 1

                for attempt in range(attempts):
                    try:
                        response = requests.request(
                            method_upper,
                            url,
                            data=body if payload is not None else None,
                            headers=headers,
                            timeout=self._timeout(timeout),
                            allow_redirects=False,
                        )
                    except requests.RequestException as exc:
                        message = (
                            f"Remote request failed for {method_upper} {request_path} "
                            f"at {base_url} via {mode} auth: {exc}"
                        )
                        self._set_last_error(message)
                        logger.warning(message)
                        if attempt < attempts - 1:
                            time.sleep(1 + attempt)
                            continue
                        break

                    last_response = response
                    status = response.status_code

                    if 300 <= status < 400:
                        location = response.headers.get("Location", "")
                        self._set_last_error(
                            (
                                f"Remote API at {base_url} redirected {method_upper} "
                                f"{request_path} to {location or 'another URL'} "
                                f"(HTTP {status}). Configure the managed server API URL "
                                "to a reachable API endpoint; IP-based HTTPS often fails "
                                "when no certificate exists for the IP address."
                            ),
                            response=response,
                        )
                        logger.warning(self.last_error)
                        redirected = True
                        break

                    if (
                        method_upper in SAFE_METHODS
                        and status in network_retry_statuses
                        and attempt < attempts - 1
                    ):
                        self._set_last_error(
                            f"Remote API returned retryable HTTP {status} for {method_upper} {request_path}.",
                            response=response,
                        )
                        time.sleep(1 + attempt)
                        continue

                    has_more_modes = index < len(modes) - 1
                    if has_more_modes and status in auth_retry_statuses:
                        self._set_last_error(
                            (
                                f"Remote API returned HTTP {status} for {method_upper} "
                                f"{request_path} via {mode} auth; trying next auth mode."
                            ),
                            response=response,
                        )
                        logger.warning(self.last_error)
                        break

                    if retry_auth and status in (401, 403) and not has_more_modes:
                        # If we failed all modes with 401/403, try one last auto-auth retry.
                        if self.auto_authenticate():
                            return self._request(
                                method_upper,
                                path,
                                payload=payload,
                                params=params,
                                timeout=timeout,
                                retry_auth=False,
                            )

                    if status >= 400:
                        self._set_last_error(
                            f"Remote API returned HTTP {status} for {method_upper} {request_path}.",
                            response=response,
                        )

                    return response

                if redirected:
                    break

        if not self.last_error and last_response is not None:
            self._set_last_error(
                f"Remote API returned HTTP {last_response.status_code} for {method_upper} {request_path}.",
                response=last_response,
            )
        elif not self.last_error:
            self._set_last_error(
                f"Remote request failed for {method_upper} {request_path}: no response."
            )

        return last_response

    def _response_error(self, fallback: str, response: requests.Response | None = None) -> str:
        if self.last_error:
            return self.last_error
        if response is not None:
            return (
                f"{fallback}: HTTP {response.status_code}. "
                f"{_safe_error_snippet(getattr(response, 'text', ''))}"
            ).strip()
        return fallback

    def _parse_json_response(self, response: requests.Response, context: str):
        try:
            return response.json()
        except ValueError:
            self._set_last_error(
                f"Remote API returned non-JSON response while {context}.",
                response=response,
            )
            logger.error(self.last_error)
            return None

    def _search_remote_service(self, service: Service, path: str) -> Optional[str]:
        resp = self._request("GET", path, params={"search": service.name}, timeout=15)
        if resp is None:
            logger.error(
                "Failed to search service %s on remote %s: %s",
                service.name,
                self.server.host,
                self.describe_last_error(),
            )
            return None

        if resp.status_code != 200:
            logger.error(
                "Failed to search service %s on remote %s: %s",
                service.name,
                self.server.host,
                self._response_error("service search failed", resp),
            )
            return None

        results = self._parse_json_response(resp, "searching remote services")
        if results is None:
            return None
        if isinstance(results, dict):
            results = results.get("results", [])
        if not isinstance(results, list):
            self._set_last_error("Remote API returned an invalid services list.")
            return None

        for remote_svc in results:
            if not isinstance(remote_svc, dict):
                continue
            if remote_svc.get("name") == service.name:
                logger.info(
                    "Found existing service %s on remote %s",
                    service.name,
                    self.server.host,
                )
                return remote_svc.get("id")

        return ""

    def sync_service(self, service: Service) -> Optional[str]:
        """
        Ensure the service exists on the remote server.
        Returns the remote service ID (UUID string) on success.
        """
        path = "/api/v1/services/"

        # 1. Search for service by name on remote. If the search fails, do not
        # POST a create request; connectivity/auth failures should not become
        # duplicate-service risk.
        try:
            existing_id = self._search_remote_service(service, path)
            if existing_id:
                return existing_id
            if existing_id is None:
                return None
        except Exception as e:
            self._set_last_error(f"Failed to search service on remote {self.server.host}: {e}")
            logger.warning(self.last_error)
            return None

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
                data = self._parse_json_response(resp, "creating remote service")
                if not isinstance(data, dict) or not data.get("id"):
                    self._set_last_error(
                        "Remote service create response did not include an id.",
                        response=resp,
                    )
                    return None
                remote_id = data["id"]
                
                # Sync environment variables
                self.sync_env_vars(service, remote_id)
                return remote_id

            if resp is not None:
                self._set_last_error("Failed to create service on remote.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error creating service on remote: {e}")
            logger.error(self.last_error)
            
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
                resp = self._request("POST", path, payload=payload, timeout=10)
                if resp is not None and resp.status_code >= 400:
                    logger.warning(
                        "Failed to sync env var %s to remote service %s: %s",
                        var.key,
                        remote_service_id,
                        self._response_error("env var sync failed", resp),
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to sync env var %s to remote service %s: %s",
                    var.key,
                    remote_service_id,
                    exc,
                )

    def trigger_deploy(self, deployment, remote_service_id, skip_review=False):
        """Trigger a deployment task on the remote server."""
        from apps.deployments.models import PlatformConfig
        path = f"/api/v1/services/{remote_service_id}/deploy/"
        config = PlatformConfig.load()
        ref = deployment.commit_hash or "HEAD"

        payload = {
            "ref": ref,
            "source_node": config.server_ip or "controller",
            "skip_review": skip_review
        }
        
        try:
            resp = self._request("POST", path, payload=payload, timeout=15)
            if resp and resp.status_code in (201, 200, 202):
                data = self._parse_json_response(resp, "triggering remote deploy")
                if isinstance(data, dict):
                    remote_id = data.get("deployment_id") or data.get("id")
                    if remote_id:
                        return remote_id
                self._set_last_error(
                    "Remote deploy trigger response did not include a deployment id.",
                    response=resp,
                )
                return None
            if resp is not None:
                self._set_last_error("Failed to trigger remote deploy.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error triggering remote deploy: {e}")
            logger.error(self.last_error)
            
        return None

    def approve_deployment(self, remote_deployment_id: str, payload: dict | None = None) -> bool:
        """Approve a paused remote REVIEW deployment."""
        path = f"/api/v1/deployments/{remote_deployment_id}/approve/"

        try:
            resp = self._request("POST", path, payload=payload or {}, timeout=15)
            if resp and resp.status_code in (200, 202):
                return True
            if resp is not None:
                self._set_last_error("Failed to approve remote deploy.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error approving remote deploy: {e}")
            logger.error(self.last_error)

        return False

    def poll_deployment(self, remote_deployment_id: str) -> dict:
        """Fetch the current status and logs of a remote deployment."""
        path = f"/api/v1/deployments/{remote_deployment_id}/"
        
        try:
            resp = self._request("GET", path, timeout=10)
            if resp and resp.status_code == 200:
                data = self._parse_json_response(resp, "polling remote deployment")
                return data if isinstance(data, dict) else {}
            if resp is not None:
                self._set_last_error("Failed to poll remote deployment.", response=resp)
        except Exception as exc:
            self._set_last_error(f"Error polling remote deployment: {exc}")
            
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
