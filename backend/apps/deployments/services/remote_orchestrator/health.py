import logging
import time

import requests

from ..tls_verify import should_verify

logger = logging.getLogger(__name__)

import os
_REMOTE_VERIFY = os.environ.get("SMSLY_REMOTE_VERIFY", "true").lower() not in (
    "0", "false", "no", "off",
)


def _is_internal_target(url: str) -> bool:
    import ipaddress
    from urllib.parse import urlparse
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


class HealthMixin:
    def check_connectivity(self) -> dict:
        results = {
            "network": False,
            "auth": False,
            "error": "",
            "latency_ms": 0,
            "base_url": "",
        }

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

        api_resp = self._request("GET", "/api/v1/services/", timeout=10)
        if api_resp is not None and api_resp.status_code == 200:
            results["auth"] = True
        else:
            results["error"] = self.describe_last_error() or f"API returned {api_resp.status_code if api_resp else 'no response'}"

        return results

    def preflight_check_or_heal(self) -> dict:
        result = {
            'ok': False,
            'healed': False,
            'error': '',
            'diagnosis': '',
        }

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
                time.sleep(15)
                post_heal = self.check_connectivity()
                if post_heal['auth']:
                    result['ok'] = True
                    logger.info(
                        "SSH auto-heal succeeded for %s (%s) — stack is back online.",
                        self.server.name, self.server.host,
                    )
                    return result

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
        from ..ssh_client import SSHClient
        if not self.server.ssh_key and not self.server.ssh_password:
            logger.warning(
                "Cannot SSH auto-heal %s: no SSH credentials stored.",
                self.server.host,
            )
            return False

        try:
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
