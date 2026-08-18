import hashlib
import hmac as hmac_mod
import json
import logging
import secrets
import time
from urllib.parse import urlencode, urlparse

import requests

from ..tls_verify import should_verify

logger = logging.getLogger(__name__)

REMOTE_RESPONSE_SNIPPET_CHARS = 1200
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "DELETE"}


def _host_is_ip(host_port: str) -> bool:
    host = host_port.rsplit(":", 1)[0] if host_port.count(":") == 1 else host_port
    host = host.strip("[]")
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _is_node_server(server) -> bool:
    if getattr(server, "is_primary", False):
        return False
    if getattr(server, "is_lite_agent", False):
        return False
    return True


def _is_internal_target(url: str) -> bool:
    import ipaddress
    parsed = urlparse(str(url or ""))
    host = parsed.hostname or ""
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


def _safe_error_snippet(value: object, limit: int = REMOTE_RESPONSE_SNIPPET_CHARS) -> str:
    import re
    text = str(value or "").replace("\x00", "")
    text = re.sub(
        r"(?i)((?:authorization|api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;}{]+",
        r"\1***",
        text,
    )
    return text[:limit]


class RemoteClientMixin:
    def _get_headers(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        auth_mode: str | None = None,
    ) -> dict:
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

        import os
        _ENFORCE_TLS = os.environ.get("SMSLY_ENFORCE_INTERSERVER_TLS", "true").lower() in (
            "1", "true", "yes", "on",
        )

        enforce_tls = (
            _ENFORCE_TLS
            and not _is_internal_target(host_port)
            and not getattr(self.server, 'is_lite_agent', False)
            and not _is_node_server(self.server)
        )

        has_explicit_port = host_port.count(":") == 1

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
                if not _is_node_server(self.server):
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
        from ..tls_verify import should_verify as _should_verify

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
                    request_path,
                    body=body,
                    auth_mode=None if mode == "none" else mode,
                )
                attempts = 2 if method_upper in SAFE_METHODS else 1

                for attempt in range(attempts):
                    try:
                        verify_ssl = _should_verify(url)
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
                        if self.auto_authenticate():
                            return self._request(
                                method_upper,
                                path,
                                payload=payload,
                                params=params,
                                timeout=timeout,
                                retry_auth=False,
                            )

                    if method_upper in SAFE_METHODS and status == 404:
                        self._enrich_404_error(response, base_url)
                        logger.warning(
                            "Trying next base URL after 404 for %s %s at %s (diagnosis: %s)",
                            method_upper, request_path, base_url,
                            self._classify_404_response(response),
                        )
                        redirected = True
                        break

                    if method_upper in SAFE_METHODS and status == 400:
                        classification_400 = self._classify_400_response(response)
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
                            break

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
