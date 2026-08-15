import logging
import re

from ..ssh_client import SSHClient

logger = logging.getLogger(__name__)


class AuthMixin:
    def auto_authenticate(self) -> bool:
        if not self.server.ssh_key and not self.server.ssh_password:
            logger.warning("No SSH credentials for server %s; cannot auto-authenticate.", self.server.host)
            return False

        logger.info("Starting SSH auto-authentication for %s", self.server.host)
        ssh = SSHClient(
            ip=self.server.host,
            key_content=self.server.ssh_key,
            key_passphrase=self.server.ssh_key_passphrase,
            password=self.server.ssh_password,
            user=self.server.ssh_user,
            port=self.server.ssh_port,
            wg_address=self.server.wg_address,
        )
        try:
            ssh.connect()
            hosting_path = ssh.find_hosting_path()

            output = ssh.run_diagnose_nodes_fix(hosting_path)
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
        import hashlib
        import hmac as hmac_mod
        import os
        import secrets
        import time

        import requests

        gateway_secret = str(self.server.gateway_secret or "").strip()
        if not gateway_secret:
            return False

        _REMOTE_VERIFY = os.environ.get("SMSLY_REMOTE_VERIFY", "true").lower() not in (
            "0", "false", "no", "off",
        )

        path = "/api/v1/auth/node-token-exchange-hmac/"
        body = self._encode_json({
            "node_name": f"Node-{self.server.host or self.server.name}"[:100],
        })
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(16)
        body_hash = hashlib.sha256(body).hexdigest()
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
            from .client import _safe_error_snippet
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
        if not str(self.server.gateway_secret or "").strip():
            return False

        candidate_urls = base_urls or self._candidate_base_urls()
        for base_url in candidate_urls:
            if self._exchange_gateway_secret_for_token(base_url):
                return True

        if self.auto_authenticate():
            for base_url in candidate_urls:
                if self._exchange_gateway_secret_for_token(base_url):
                    return True

        return False
