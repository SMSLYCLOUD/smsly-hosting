import hashlib
import hmac as hmac_mod
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import time
import uuid
from urllib.parse import urlencode, urlparse

import requests

from apps.deployments.models import (  # type: ignore[attr-defined]
    EnvironmentVariable,
    ManagedServer,
    PlatformConfig,
    Service,
)

from .ssh_client import SSHClient
from .tls_verify import should_verify

logger = logging.getLogger(__name__)

# SEC-ZT-005: Inter-server TLS enforcement.
# Enforce TLS by default. Set SMSLY_ENFORCE_INTERSERVER_TLS=false to bypass (insecure).
_ENFORCE_TLS = os.environ.get("SMSLY_ENFORCE_INTERSERVER_TLS", "true").lower() in (
    "1", "true", "yes", "on",
)
# SEC-ZT-005: TLS certificate verification for inter-server requests.
# Set SMSLY_REMOTE_VERIFY=0 to disable (self-signed certs, lab environments).
_REMOTE_VERIFY = os.environ.get("SMSLY_REMOTE_VERIFY", "true").lower() not in (
    "0", "false", "no", "off",
)


def _host_is_ip(host_port: str) -> bool:
    host = host_port.rsplit(":", 1)[0] if host_port.count(":") == 1 else host_port
    host = host.strip("[]")
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_node_server(server) -> bool:
    """Return True when the server is a full-stack node (not primary, not lite).

    Full-stack nodes run Traefik on port 80 but do NOT run Caddy, so they
    have no HTTPS listener.  TLS enforcement should be skipped for these
    servers to avoid HTTP 400 errors from the orchestrator trying HTTPS.
    """
    if getattr(server, "is_primary", False):
        return False  # Primary has Caddy → HTTPS available
    if getattr(server, "is_lite_agent", False):
        return False  # Lite agents are handled separately
    return True  # Full-stack node → HTTP only (Traefik)


def _is_internal_target(url: str) -> bool:
    """Return True when a remote URL should use mesh/IP transport semantics."""
    import ipaddress
    parsed = urlparse(str(url or ""))
    host = parsed.hostname or ""
    # Only treat RFC 1918 / CGNAT / loopback / link-local IPs as internal.
    # Public IPs must use full TLS verification.
    try:
        addr = ipaddress.ip_address(host)
        is_private = addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        is_private = host == "localhost"
    is_internal = is_private
    logger.debug(
        "is_internal_target url=%s host=%s is_internal=%s",
        url,
        host,
        is_internal,
    )
    return is_internal


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
        # Always re-fetch the server from DB to ensure we get the freshest
        # api_token — EncryptedCharField can return empty in certain Celery
        # task contexts if the passed-in instance was stale or pickled.
        try:
            fresh = ManagedServer.objects.only(
                "api_token", "gateway_secret", "api_url", "host",
                "ssh_key", "ssh_password", "ssh_user", "ssh_port",
            ).get(id=server.id if isinstance(server.id, uuid.UUID) else server.pk)
            self.server = fresh
        except Exception:
            self.server = server
        self.base_url = (self.server.api_url or f"http://{self.server.host}").rstrip('/')
        self.last_error = ""
        logger.info(
            "RemoteOrchestrator initialized for %s (%s)",
            self.server.name, self.server.host,
        )

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

    def check_connectivity(self) -> dict:
        """
        Perform a tiered connectivity check:
        1. Ping /health (Network)
        2. GET /api/v1/services/ (Auth/Application)
        """
        results = {
            "network": False,
            "auth": False,
            "error": "",
            "latency_ms": 0,
            "base_url": "",
        }

        # 1. Network Check. Try every candidate URL before declaring the node
        # unreachable; mesh routes can be stale while the public node endpoint
        # is still healthy.
        base_urls = self._candidate_base_urls()
        if not base_urls:
            results["error"] = "No candidate base URLs found."
            return results

        health_paths = ("/health", "/health/live")
        health_errors: list[str] = []
        for base_url in base_urls:
            for health_path in health_paths:
                try:
                    start = time.time()
                    health_url = f"{base_url}{health_path}"
                    verify_health = _REMOTE_VERIFY if health_url.startswith("https://") else False
                    if _is_internal_target(health_url):
                        verify_health = False
                    resp = requests.get(health_url, timeout=10, verify=verify_health, allow_redirects=False)
                    results["latency_ms"] = int((time.time() - start) * 1000)

                    if resp.status_code < 500:
                        results["network"] = True
                        results["base_url"] = base_url
                        break
                    health_errors.append(f"{health_url} -> HTTP {resp.status_code}")
                except requests.RequestException as e:
                    health_errors.append(f"{health_url} -> {e}")
            if results["network"]:
                break

        if not results["network"]:
            results["error"] = "Network unreachable: " + "; ".join(health_errors)
            return results

        # 2. Auth/API Check
        api_resp = self._request("GET", "/api/v1/services/", timeout=10)
        if api_resp is not None and api_resp.status_code == 200:
            results["auth"] = True
        else:
            results["error"] = self.describe_last_error() or f"API returned {api_resp.status_code if api_resp else 'no response'}"

        return results

    def preflight_check_or_heal(self) -> dict:
        """Pre-flight connectivity check with optional SSH auto-healing.

        Call this before delegating a deployment to verify the remote
        node's API is actually reachable.  When the API is down (e.g.
        Traefik returns its default 404 because the backend container
        crashed), the method will attempt to restart the entire
        docker-compose stack via SSH.

        Returns::

            {
                'ok':        bool,  # whether the API is reachable now
                'healed':    bool,  # whether SSH repair was attempted
                'error':     str,   # human-readable error (empty when ok)
                'diagnosis': str,   # e.g. 'traefik_no_router'
            }
        """
        result = {
            'ok': False,
            'healed': False,
            'error': '',
            'diagnosis': '',
        }

        # Step 1: Quick connectivity check
        connectivity = self.check_connectivity()
        if not connectivity['network']:
            result['error'] = (
                f"Remote node {self.server.name} ({self.server.host}) is "
                f"network-unreachable: {connectivity['error']}"
            )
            result['diagnosis'] = 'network_unreachable'
            return result

        if connectivity['auth']:
            result['ok'] = True
            return result

        # Step 2: Diagnose what returned the error
        probe = self._request(
            'GET', '/api/v1/services/', timeout=10, retry_auth=False,
        )
        if probe is not None and probe.status_code == 404:
            classification = self._classify_404_response(probe)
            result['diagnosis'] = classification
            diagnosis_msg = self._404_DIAGNOSIS_MESSAGES.get(classification, '')
        elif probe is not None and probe.status_code == 400:
            classification = self._classify_400_response(probe)
            result['diagnosis'] = classification
            diagnosis_msg = self._400_DIAGNOSIS_MESSAGES.get(classification, '')
        else:
            classification = 'auth_or_other'
            diagnosis_msg = connectivity.get('error', self.describe_last_error())
            result['diagnosis'] = classification

        # Step 3: Attempt SSH auto-heal for proxy-level errors
        # Previously only 'traefik_no_router' (404) triggered auto-heal.
        # Now also handle 400 errors that indicate proxy misconfiguration
        # (e.g. TLS mismatch, Traefik bad request) which a stack restart
        # may resolve.
        healable_classifications = {
            'traefik_no_router',
            'tls_mismatch',
            'traefik_bad_request',
            'proxy_html_400',
        }
        if classification in healable_classifications:
            logger.warning(
                "Remote node %s (%s) has Traefik running but backend is "
                "unreachable. Attempting SSH auto-heal (full stack restart)...",
                self.server.name, self.server.host,
            )
            healed = self._ssh_restart_stack()
            result['healed'] = True
            if healed:
                # Re-check connectivity after heal
                time.sleep(15)  # Give the entire stack time to start
                post_heal = self.check_connectivity()
                if post_heal['auth']:
                    result['ok'] = True
                    logger.info(
                        "SSH auto-heal succeeded for %s (%s) — stack is back online.",
                        self.server.name, self.server.host,
                    )
                    return result

                # Still starting — give more time
                time.sleep(15)
                post_heal2 = self.check_connectivity()
                if post_heal2['auth']:
                    result['ok'] = True
                    logger.info(
                        "SSH auto-heal succeeded for %s (%s) after extended wait.",
                        self.server.name, self.server.host,
                    )
                    return result

                result['error'] = (
                    f"SSH auto-heal restarted the stack on {self.server.host}, "
                    f"but the API is still unreachable after 30 seconds. "
                    f"The node may need manual investigation."
                )
            else:
                result['error'] = (
                    f"Backend is down on {self.server.host} (Traefik 404) and "
                    f"SSH auto-heal failed. No SSH credentials or the restart "
                    f"command failed. Manual fix: ssh into the node and run "
                    f"'cd /opt/smsly-hosting && docker compose up -d'"
                )
        else:
            result['error'] = (
                f"Remote node {self.server.name} ({self.server.host}) API check "
                f"failed: {diagnosis_msg}"
            )

        return result

    def _ssh_restart_stack(self) -> bool:
        """Attempt to restart the entire docker-compose stack on the remote node via SSH."""
        if not self.server.ssh_key and not self.server.ssh_password:
            logger.warning(
                "Cannot SSH auto-heal %s: no SSH credentials stored.",
                self.server.host,
            )
            return False

        try:
            from .ssh_client import SSHClient
            ssh = SSHClient(
                ip=self.server.host,
                key_content=self.server.ssh_key,
                password=self.server.ssh_password,
                user=self.server.ssh_user,
                port=self.server.ssh_port,
                wg_address=self.server.wg_address,
            )
            ssh.connect()
            success, output = ssh.restart_stack()
            ssh.close()
            if success:
                logger.info(
                    "SSH auto-heal: stack restarted on %s. Output: %s",
                    self.server.host, output[:500],
                )
            else:
                logger.warning(
                    "SSH auto-heal: stack restart failed on %s. Output: %s",
                    self.server.host, output[:500],
                )
            return success
        except Exception as exc:
            logger.error(
                "SSH auto-heal exception for %s: %s",
                self.server.host, exc,
            )
            return False

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
            port=self.server.ssh_port,
            wg_address=self.server.wg_address,
        )
        try:
            ssh.connect()
            hosting_path = ssh.find_hosting_path()

            # 1. Get/Create API Token
            output = ssh.run_diagnose_nodes_fix(hosting_path)
            # Match both smsly_ tokens and standard DRF tokens
            token_match = re.search(r"TOKEN:\s+([a-zA-Z0-9_]+)", output)
            new_token = token_match.group(1) if token_match else None

            if not new_token:
                logger.info("diagnose_nodes --fix did not produce a token; trying drf_create_token fallback for %s", self.server.host)
                new_token = ssh.create_api_token(hosting_path)

            updated = False
            if new_token and self.server.api_token != new_token:
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

    def _exchange_gateway_secret_for_token(self, base_url: str) -> bool:
        """
        Use the stored remote GATEWAY_SECRET to mint and persist a fresh API token.

        HMAC can authenticate remote sync requests, but a saved smsly_ token keeps
        future calls fast and avoids repeated signed fallbacks.
        """
        gateway_secret = str(self.server.gateway_secret or "").strip()
        if not gateway_secret:
            return False

        path = "/api/v1/auth/node-token-exchange-hmac/"
        body = self._encode_json({
            "node_name": f"Node-{self.server.host or self.server.name}"[:100],
        })
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        body_hash = hashlib.sha256(body).hexdigest()
        # SECURITY: bind the nonce into the signed payload so a
        # captured request cannot be replayed with a fresh nonce.
        # Matches views_node_exchange.node_token_exchange_via_gateway.
        payload = f"POST|{path}|{timestamp}|{nonce}|{body_hash}"
        signature = hmac_mod.new(
            gateway_secret.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        url = f"{base_url.rstrip('/')}{path}"
        verify_ssl = _REMOTE_VERIFY if url.startswith("https://") else False

        try:
            response = requests.request(
                "POST",
                url,
                data=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Gateway-Signature-V2": signature,
                    "X-Request-Timestamp": timestamp,
                    "X-Request-Nonce": nonce,
                },
                timeout=self._timeout(15),
                allow_redirects=False,
                verify=verify_ssl,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Gateway token exchange request failed for %s at %s: %s",
                self.server.host,
                base_url,
                exc,
            )
            return False

        if response.status_code != 200:
            logger.warning(
                "Gateway token exchange failed for %s at %s: HTTP %s. %s",
                self.server.host,
                base_url,
                response.status_code,
                _safe_error_snippet(getattr(response, "text", "")),
            )
            return False

        try:
            data = response.json()
        except ValueError:
            logger.warning(
                "Gateway token exchange for %s returned non-JSON response.",
                self.server.host,
            )
            return False

        token = str(data.get("token") or "").strip() if isinstance(data, dict) else ""
        if not token:
            logger.warning(
                "Gateway token exchange for %s returned no token.",
                self.server.host,
            )
            return False

        if self.server.api_token != token:
            self.server.api_token = token
            self.server.save(update_fields=["api_token", "updated_at"])
        logger.info(
            "Gateway token exchange refreshed API token for %s (%s).",
            self.server.name,
            self.server.host,
        )
        return True

    def _try_gateway_token_exchange(self, base_urls: list[str] | None = None) -> bool:
        """Try token exchange against candidate remote API base URLs.

        If the HMAC exchange fails (e.g. stale gateway_secret), attempt
        SSH-based secret re-sync and retry once before giving up.
        """
        if not str(self.server.gateway_secret or "").strip():
            return False

        candidate_urls = base_urls or self._candidate_base_urls()
        for base_url in candidate_urls:
            if self._exchange_gateway_secret_for_token(base_url):
                return True

        # HMAC exchange failed — likely a stale gateway_secret.
        # Re-sync the secret via SSH and retry once.
        if self.auto_authenticate():
            for base_url in candidate_urls:
                if self._exchange_gateway_secret_for_token(base_url):
                    return True

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
            nonce = secrets.token_urlsafe(16)
            body_hash = hashlib.sha256(body).hexdigest()
            # SECURITY: bind the nonce into the signed payload so a
            # captured request cannot be replayed with a fresh nonce.
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
        """Return API base URLs worth trying without mutating the saved server.

        Priority order:
          1. WireGuard mesh VPN IP (internal, encrypted by WireGuard) — HTTP only
          2. Public IP / Domain (TLS enforced where applicable)
        """
        urls: list[str] = []

        def append(value: str):
            normalized = str(value or "").strip().rstrip("/")
            if normalized and normalized not in urls:
                urls.append(normalized)

        host_port = str(getattr(self.server, "host", "") or "").strip().rstrip("/")
        if "://" in host_port:
            parsed = urlparse(host_port)
            host_port = parsed.netloc or parsed.path
        host_port = host_port.split("/", 1)[0].strip()

        wg_ip = str(getattr(self.server, "wg_address", "") or "").strip()
        has_wg = bool(wg_ip and wg_ip != host_port)
        is_lite = getattr(self.server, 'is_lite_agent', False)

        # ── Priority 1: WireGuard Mesh VPN (secure, internal, encrypted) ──
        if has_wg:
            if is_lite:
                append(f"http://{wg_ip}:8000")
                append(f"http://{wg_ip}")
                append(f"http://{wg_ip}:8090")
            else:
                append(f"http://{wg_ip}:8000")
                append(f"http://{wg_ip}:8090")
                append(f"http://{wg_ip}")

        if not host_port:
            return urls

        # Internal mesh VPN IPs should use HTTP (encryption handled by WireGuard/ZeroTier).
        # Lite agents on WireGuard mesh also skip TLS — they may not have HTTPS on 443.
        # Full-stack nodes (non-primary, non-lite) only have Traefik on HTTP — no Caddy.
        enforce_tls = (
            _ENFORCE_TLS
            and not _is_internal_target(host_port)
            and not getattr(self.server, 'is_lite_agent', False)
            and not _is_node_server(self.server)
        )

        has_explicit_port = host_port.count(":") == 1

        # ── Priority 2: Public IP / Domain (fallback) ──
        if _host_is_ip(host_port):
            if getattr(self.server, 'is_lite_agent', False):
                if not enforce_tls:
                    append(f"http://{host_port}")
                    append(f"http://{host_port}:8090")
                append(f"https://{host_port}")
            else:
                if not enforce_tls:
                    append(f"http://{host_port}:8090")
                    append(f"http://{host_port}")
                append(f"https://{host_port}")
        else:
            append(self.base_url)
            append(f"https://{host_port}")
            if not enforce_tls:
                append(f"http://{host_port}")
                if not has_explicit_port:
                    append(f"http://{host_port}:8090")

        if enforce_tls and urls:
            https_urls = [u for u in urls if u.startswith("https://")]
            if https_urls:
                return https_urls

        return urls

    @staticmethod
    def _filter_reachable(urls: list[str], probe_timeout: float = 1.0) -> list[str]:
        """Pre-filter candidate URLs by fast TCP connect probe. Skip dead endpoints."""
        import socket as sock_module
        reachable: list[str] = []
        for url in urls:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            if not host:
                reachable.append(url)
                continue
            try:
                with sock_module.create_connection((host, port), timeout=probe_timeout):
                    reachable.append(url)
            except (TimeoutError, OSError):
                logger.debug("Pre-filter skipped unreachable %s", url)
        if not reachable and urls:
            logger.warning("All %d candidate URLs unreachable: %s", len(urls), urls[:3])
        return reachable

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
        base_urls = self._filter_reachable(self._candidate_base_urls())

        if retry_auth and (not modes or modes == ["none"]):
            # If no auth modes, try auto-auth first
            if self.auto_authenticate():
                modes = self._auth_modes()
            if not modes or modes == ["none"]:
                self._set_last_error(

                        "Remote API credentials are missing for this managed server. "
                        "The controller has no api_token or gateway_secret and could not "
                        "repair them over SSH. Reconnect or retry provisioning the node so "
                        "a node token and gateway secret are stored before deploying."

                )
                logger.error(self.last_error)
                return None

        if retry_auth and "token" not in modes and "hmac" in modes:
            if self._try_gateway_token_exchange(base_urls):
                modes = self._auth_modes()

        for base_url in base_urls:
            url = f"{base_url}{request_path}"
            redirected = False

            for index, mode in enumerate(modes):
                headers = self._get_headers(
                    method_upper,
                    request_path,  # Use full path with query params to match server-side get_full_path()
                    body=body,
                    auth_mode=None if mode == "none" else mode,
                )
                attempts = 2 if method_upper in SAFE_METHODS else 1

                for attempt in range(attempts):
                    try:
                        # SEC-ZT-005 + MESH-OPTIMIZATION: TLS verification logic.
                        # Centralised in ``apps.deployments.services.tls_verify``:
                        # plain HTTP, loopback, Docker-internal, and private
                        # IPs (when ALLOW_INSECURE_INTER_NODE_TLS is set) get
                        # ``verify=False``; HTTPS public URLs get ``verify=True``.
                        verify_ssl = should_verify(url)
                        # Follow redirects (e.g. Traefik trailing-slash 308) so the
                        # remote API is reachable even when the proxy normalises
                        # paths before forwarding.
                        response = requests.request(
                            method_upper,
                            url,
                            data=body if payload is not None else None,
                            headers=headers,
                            timeout=self._timeout(timeout),
                            allow_redirects=True,
                            verify=verify_ssl,
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
                    if (
                        retry_auth
                        and mode == "token"
                        and status in auth_retry_statuses
                        and str(self.server.gateway_secret or "").strip()
                    ) and self._try_gateway_token_exchange([base_url]):
                        return self._request(
                            method_upper,
                            path,
                            payload=payload,
                            params=params,
                            timeout=timeout,
                            retry_auth=False,
                        )

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

                    # For safe methods, a 404 means we hit the wrong port/service —
                    # try the next candidate base URL instead of returning immediately.
                    if method_upper in SAFE_METHODS and status == 404:
                        self._enrich_404_error(response, base_url)
                        logger.warning(
                            "Trying next base URL after 404 for %s %s at %s (diagnosis: %s)",
                            method_upper, request_path, base_url,
                            self._classify_404_response(response),
                        )
                        redirected = True
                        break  # L3 break → L2 break via redirected → next base URL

                    # For safe methods, a 400 may indicate TLS mismatch (HTTPS
                    # request hit an HTTP-only node).  Try the next candidate
                    # base URL so we can fall back to HTTP.
                    if method_upper in SAFE_METHODS and status == 400:
                        classification_400 = self._classify_400_response(response)
                        # Only try next URL for proxy-level 400s, not app-level.
                        # App-level 400s (e.g. validation errors) are meaningful
                        # and should be returned to the caller.
                        proxy_400s = {'tls_mismatch', 'traefik_bad_request', 'proxy_html_400'}
                        if classification_400 in proxy_400s:
                            diagnosis_400 = self._400_DIAGNOSIS_MESSAGES.get(classification_400, '')
                            self._set_last_error(
                                f"Remote API returned HTTP 400 at {base_url}. "
                                f"Diagnosis ({classification_400}): {diagnosis_400}",
                                response=response,
                            )
                            logger.warning(
                                "Trying next base URL after 400 for %s %s at %s (diagnosis: %s)",
                                method_upper, request_path, base_url,
                                classification_400,
                            )
                            redirected = True
                            break  # L3 break → L2 break via redirected → next base URL

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

    @staticmethod
    def _classify_404_response(response) -> str:
        """Classify what service returned a 404 to diagnose the root cause.

        Returns one of:
          - 'traefik_no_router': Traefik running but no backend router
          - 'django_not_found': Django endpoint does not exist
          - 'proxy_html_404': Nginx or similar proxy returned an HTML 404
          - 'unknown_404': Unrecognised 404 format
        """
        body = (getattr(response, 'text', '') or '').strip()
        body_lower = body.lower()
        if body_lower == '404 page not found':
            return 'traefik_no_router'
        if '"detail"' in body_lower and '"not found' in body_lower:
            return 'django_not_found'
        if '<html' in body_lower or '<!doctype' in body_lower:
            return 'proxy_html_404'
        return 'unknown_404'

    _404_DIAGNOSIS_MESSAGES = {
        'traefik_no_router': (
            'Traefik is running on the remote node but no router matched '
            '/api/v1/. The backend container is most likely down, not on '
            'the smsly-net Docker network, or its Traefik labels are missing.'
        ),
        'django_not_found': (
            'The remote Django API is reachable but returned a 404 for this '
            'endpoint. This may indicate a version mismatch between the '
            'controller and agent codebases.'
        ),
        'proxy_html_404': (
            'A reverse proxy (Nginx/Caddy) on the remote node returned an '
            'HTML 404 page. The proxy may be misconfigured or the backend '
            'upstream is unreachable.'
        ),
        'unknown_404': 'The remote node returned an unrecognised 404 response.',
    }

    @staticmethod
    def _classify_400_response(response) -> str:
        """Classify what service returned a 400 to diagnose the root cause.

        Returns one of:
          - 'tls_mismatch': HTTPS request hit an HTTP-only service (proxy 400)
          - 'traefik_bad_request': Traefik returned a 400 (e.g. bad Host header)
          - 'proxy_html_400': Proxy returned an HTML 400 page
          - 'unknown_400': Unrecognised 400 format
        """
        body = (getattr(response, 'text', '') or '').strip()
        body_lower = body.lower()
        if '<html' in body_lower or '<!doctype' in body_lower:
            if 'bad request' in body_lower:
                return 'tls_mismatch'
            return 'proxy_html_400'
        if '400 bad request' in body_lower:
            return 'tls_mismatch'
        if body_lower.startswith('400'):
            return 'traefik_bad_request'
        return 'unknown_400'

    _400_DIAGNOSIS_MESSAGES = {
        'tls_mismatch': (
            'An HTTPS request was sent to an HTTP-only service. This happens '
            'when the orchestrator tries TLS on a node that only has Traefik '
            '(no Caddy). The wg_address or api_url should use HTTP.'
        ),
        'traefik_bad_request': (
            'Traefik returned a 400 Bad Request, likely due to a malformed '
            'Host header or an unsupported request. The backend may be down '
            'or the Traefik routing configuration is incorrect.'
        ),
        'proxy_html_400': (
            'A reverse proxy on the remote node returned an HTML 400 page. '
            'The proxy may be misconfigured or the backend upstream is '
            'unreachable.'
        ),
        'unknown_400': 'The remote node returned an unrecognised 400 response.',
    }

    def _enrich_404_error(self, response, base_url: str):
        """Set a detailed last_error for 404 responses with root-cause diagnosis."""
        classification = self._classify_404_response(response)
        diagnosis = self._404_DIAGNOSIS_MESSAGES.get(classification, '')
        self._set_last_error(
            f"Remote API returned HTTP 404 at {base_url}. "
            f"Diagnosis ({classification}): {diagnosis}",
            response=response,
        )

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

    def _search_remote_service(self, service: Service, path: str) -> str | None:
        page = 1
        while True:
            resp = self._request("GET", path, params={"search": service.name, "page": page}, timeout=15)
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

            data = self._parse_json_response(resp, "searching remote services")
            if data is None:
                return None

            if isinstance(data, dict):
                results = data.get("results", [])
            else:
                results = data

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
                    return remote_svc.get("id") or ""

            # Check if there are more pages
            if isinstance(data, dict):
                next_url = data.get("next")
                if not next_url:
                    break
            else:
                break
            page += 1

        return ""

    def sync_service(self, service: Service) -> str | None:
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
                if not self._sync_remote_service_config(service, existing_id):
                    return None
                self.sync_env_vars(service, existing_id)
                return existing_id
            if existing_id is None:
                return None
        except Exception as e:
            self._set_last_error(f"Failed to search service on remote {self.server.host}: {e}")
            logger.warning(self.last_error)
            return None

        # 2. Not found -> Create it
        logger.info("Creating service %s on remote %s", service.name, self.server.host)
        payload = self._service_sync_payload(service)

        try:
            resp = self._request("POST", path, payload=payload, timeout=30)
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

    def _service_sync_payload(self, service: Service) -> dict:
        """Return the service fields a remote node must mirror exactly."""
        payload = {
            "name": service.name,
            "deploy_type": service.deploy_type,
            "repository_url": service.repository_url,
            "branch": service.branch,
            "docker_image": service.docker_image,
            "internal_port": service.internal_port,
            "is_public": service.is_public,
            "buildpack": service.buildpack,
            "public_domain": service.public_domain,
            "public_domain_hidden": service.public_domain_hidden,
            "custom_domains": service.custom_domains or [],
            "build_command": service.build_command,
            "start_command": service.start_command,
            "root_directory": service.root_directory,
            "deploy_mode": service.deploy_mode,
            "compose_file": service.compose_file,
            "compose_main_service": service.compose_main_service,
            "health_check_path": service.health_check_path,
            "health_check_port": service.health_check_port,
            "health_check_interval": service.health_check_interval,
            "health_check_timeout": service.health_check_timeout,
            "health_check_retries": service.health_check_retries,
            "restart_policy": service.restart_policy,
            "cpu_cores": str(service.cpu_cores),
            "memory_mb": service.memory_mb,
            "min_replicas": service.min_replicas,
            "max_replicas": service.max_replicas,
            "vpa_enabled": service.vpa_enabled,
        }
        return payload

    def _sync_remote_service_config(self, service: Service, remote_service_id: str) -> bool:
        """Patch an existing remote service so its routing metadata cannot drift."""
        path = f"/api/v1/services/{remote_service_id}/"
        try:
            resp = self._request(
                "PATCH",
                path,
                payload=self._service_sync_payload(service),
                timeout=15,
            )
            if resp and resp.status_code in (200, 202):
                return True

            if resp is not None:
                self._set_last_error("Failed to update service on remote.", response=resp)
            logger.error(self.last_error)
        except Exception as e:
            self._set_last_error(f"Error updating service on remote: {e}")
            logger.error(self.last_error)

        return False

    def sync_env_vars(self, service: Service, remote_service_id: str):
        """Sync environment variables to the remote service.

        Skips vars whose ORM value is still ciphertext (starts with gAAAAAB)
        to prevent sending undecrypted data to the remote node.
        """
        path = f"/api/v1/services/{remote_service_id}/env_vars/"

        def _is_ciphertext(val: str) -> bool:
            """Detect Fernet ciphertext that was not decrypted by the ORM."""
            if not val or not isinstance(val, str):
                return False
            # Fernet tokens always start with gAAAA (base64 of version byte + timestamp)
            if val.startswith("gAAAA"):
                return True
            # Additional heuristic: all-base64/base64url string of Fernet-typical length
            if len(val) > 100 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in val):
                try:
                    import base64
                    padded = val + '=' * (-len(val) % 4)
                    decoded = base64.urlsafe_b64decode(padded)
                    # Fernet tokens are at least 57 bytes (version + time + iv + 16-byte block + hmac)
                    if len(decoded) >= 57 and decoded[0] == 0x80:
                        return True
                except Exception:
                    pass
            return False

        env_vars = EnvironmentVariable.objects.filter(service=service)

        safe_vars = []
        skipped_count = 0
        for var in env_vars:
            raw_value = var.value
            if _is_ciphertext(raw_value):
                logger.warning(
                    "[DB-ENCRYPT] Skipping env var %s for service %s — "
                    "value is ciphertext (decryption failed or double-encrypted).",
                    var.key, service.name,
                )
                skipped_count += 1
                continue
            safe_vars.append({
                "key": var.key,
                "value": raw_value,
                "is_secret": var.is_secret,
                "source": var.source,
            })

        if skipped_count > 0:
            logger.warning(
                "[DB-ENCRYPT] Skipped %d environment variables for service %s due to decryption failure/ciphertext value.",
                skipped_count, service.name,
            )

        if not safe_vars:
            logger.info(
                "No safe env vars to sync for service %s (all were ciphertext).",
                service.name,
            )
            return

        payload = {"vars": safe_vars}

        try:
            resp = self._request("POST", path, payload=payload, timeout=20)
            if resp is not None and resp.status_code < 400:
                return
            if resp is not None:
                logger.warning(
                    "Bulk env sync failed for remote service %s: %s",
                    remote_service_id,
                    self._response_error("bulk env sync failed", resp),
                )
        except Exception as exc:
            logger.warning(
                "Bulk env sync failed for remote service %s: %s",
                remote_service_id,
                exc,
            )

        for var in safe_vars:
            try:
                resp = self._request("POST", path, payload=var, timeout=10)
                if resp is not None and resp.status_code >= 400:
                    logger.warning(
                        "Failed to sync env var %s to remote service %s: %s",
                        var["key"],
                        remote_service_id,
                        self._response_error("env var sync failed", resp),
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to sync env var %s to remote service %s: %s",
                    var["key"],
                    remote_service_id,
                    exc,
                )

    def trigger_deploy(self, deployment, remote_service_id, skip_review=False, image_name=None):
        """Trigger a deployment task on the remote server.

        When ``image_name`` is provided (pre-built image pushed by the master),
        the remote will skip the build phase and directly pull and run.
        This enables the build-agent optimization where the master handles
        all builds and the remote node only runs containers.
        """
        path = f"/api/v1/services/{remote_service_id}/deploy/"
        config = PlatformConfig.load()
        ref = deployment.commit_hash or "HEAD"

        payload = {
            "ref": ref,
            "source_node": config.server_ip or "controller",
            "skip_review": skip_review,
        }
        if image_name:
            # [FIX] Build-Agent Optimization: Rewrite internal registry name for remote nodes.
            # Master pushes to 'registry:5000' (internal Docker DNS).
            # ── Zero-Trust Registry Rewriting ──────────────────────────
            # If the image refers to the internal build-agent registry,
            # we must rewrite it to a reachable address for the remote node.
            internal_registry_markers = ["registry:5000", "localhost:5000", "127.0.0.1:5000"]
            if any(marker in image_name for marker in internal_registry_markers):
                from apps.deployments.services.provisioner import _get_master_mesh_ip
                master_ip = _get_master_mesh_ip() or os.environ.get("PUBLIC_IP") or "127.0.0.1"

                for marker in internal_registry_markers:
                    image_name = image_name.replace(marker, f"{master_ip}:5000")

                logger.info(f"Rewrote registry image for remote node: {image_name} (via {master_ip})")

            payload["image_name"] = image_name

        try:
            resp = self._request("POST", path, payload=payload, timeout=60)
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

    def delete_service(
        self,
        remote_service_id: str,
        *,
        force: bool = False,
        not_found_ok: bool = True,
    ) -> bool:
        """Tell the remote server to delete the given service."""
        path = f"/api/v1/services/{remote_service_id}/"
        params = {"force": "true"} if force else None

        try:
            resp = self._request("DELETE", path, params=params, timeout=20)
            # 202 Accepted, 204 No Content, 200 OK, or 404 Not Found (already gone)
            if resp and resp.status_code in (202, 204, 200):
                return True
            if resp and resp.status_code == 404 and not_found_ok:
                return True
            if resp is not None:
                self._set_last_error("Failed to delete service on remote.", response=resp)
        except Exception as e:
            self._set_last_error(f"Error deleting service on remote: {e}")
            logger.error(self.last_error)
        return False

    def delete_service_for_local(self, service: Service, *, force: bool = False) -> bool:
        """
        Delete a remote runtime for a controller-owned service.

        Remote full installs create their own Service rows, so the controller's
        local UUID is often not valid on the node. Resolve the remote row by
        exact service name first, then use SSH as a runtime cleanup fallback for
        transferred/lite-agent containers.
        """
        remote_service_id = self._search_remote_service(service, "/api/v1/services/")
        api_deleted = False

        if remote_service_id:
            api_deleted = self.delete_service(
                remote_service_id,
                force=force,
                not_found_ok=True,
            )
        elif remote_service_id == "":
            # Shared-DB lite agents may still expose the controller UUID.
            api_deleted = self.delete_service(
                str(service.id),
                force=force,
                not_found_ok=False,
            )

        ssh_deleted = False
        if not api_deleted or getattr(service, "active_runtime_id", None):
            ssh_deleted = self.delete_service_runtime_via_ssh(service)

        return bool(api_deleted or ssh_deleted or force)

    def delete_service_runtime_via_ssh(self, service: Service) -> bool:
        """Best-effort host-level cleanup for transferred/lite-agent runtimes."""
        if not (self.server.ssh_key or self.server.ssh_password):
            self._set_last_error(
                "Remote service API deletion did not complete and no SSH credentials are stored for fallback cleanup."
            )
            return False

        identifiers = []
        for raw in (
            getattr(service, "active_runtime_id", None),
            getattr(service, "name", None),
            getattr(service, "slug", None),
        ):
            value = str(raw or "").strip()
            if value and value not in identifiers:
                identifiers.append(value)

        service_id = str(getattr(service, "id", "") or "").strip()
        service_name = str(getattr(service, "name", "") or "").strip()
        service_slug = str(getattr(service, "slug", "") or "").strip()
        label_filters = []
        if service_id:
            label_filters.append(f"smsly.service_id={service_id}")
        if service_name:
            label_filters.append(f"smsly.blue_green.canonical_name={service_name}")
        if service_slug:
            label_filters.append(f"com.docker.compose.project={service_slug}")

        remove_exact = " ".join(shlex.quote(value) for value in identifiers)
        label_args = " ".join(shlex.quote(value) for value in label_filters)
        green_prefixes = " ".join(
            shlex.quote(f"{value}-green-")
            for value in (service_name, service_slug)
            if value
        )
        volume_label_args = label_args

        script = f"""
set +e
removed=0
failed=0
for ref in {remove_exact}; do
  [ -n "$ref" ] || continue
  if docker inspect "$ref" >/dev/null 2>&1; then
    docker rm -f "$ref" >/dev/null 2>&1 && removed=1 || failed=1
  fi
done
for label in {label_args}; do
  [ -n "$label" ] || continue
  for cid in $(docker ps -aq --filter "label=$label"); do
    docker rm -f "$cid" >/dev/null 2>&1 && removed=1 || failed=1
  done
done
for prefix in {green_prefixes}; do
  [ -n "$prefix" ] || continue
  for cid in $(docker ps -aq --filter "name=^/${{prefix}}"); do
    docker rm -f "$cid" >/dev/null 2>&1 && removed=1 || failed=1
  done
done
for label in {volume_label_args}; do
  [ -n "$label" ] || continue
  for vid in $(docker volume ls -q --filter "label=$label"); do
    docker volume rm -f "$vid" >/dev/null 2>&1 || true
  done
done
if [ "$failed" -eq 1 ]; then
  echo SMSLY_DELETE_FAILED
  exit 1
fi
if [ "$removed" -eq 1 ]; then
  echo SMSLY_DELETE_REMOVED
else
  echo SMSLY_DELETE_NOT_FOUND
fi
exit 0
""".strip()

        ssh = SSHClient(
            ip=self.server.host,
            key_content=self.server.ssh_key,
            password=self.server.ssh_password,
            user=self.server.ssh_user,
            port=self.server.ssh_port,
            wg_address=self.server.wg_address,
        )
        try:
            ssh.connect()
            argv = shlex.split(f"sh -lc {shlex.quote(script)}")
            out, err, code = ssh.exec_command(
                argv,
                timeout=60,
                raise_on_error=False,
            )
            output = f"{out}\n{err}".strip()
            if code == 0:
                if "SMSLY_DELETE_REMOVED" not in output:
                    self._set_last_error(
                        f"SSH runtime cleanup found no matching containers on {self.server.host}: {output[:500]}"
                    )
                    logger.warning(self.last_error)
                    return False
                logger.info(
                    "SSH runtime cleanup for service %s on %s completed: %s",
                    service.name, self.server.host, output[:500],
                )
                return True
            self._set_last_error(
                f"SSH runtime cleanup failed on {self.server.host}: {output[:500]}"
            )
            logger.error(self.last_error)
            return False
        except Exception as exc:
            self._set_last_error(
                f"SSH runtime cleanup failed on {self.server.host}: {exc}"
            )
            logger.error(self.last_error)
            return False
        finally:
            ssh.close()
