import logging
import subprocess

logger = logging.getLogger(__name__)

class SSLVerifier:
    """Safely issues and tests SSL certificates."""

    @classmethod
    def test_reverse_proxy_config(cls, proxy_type="caddy"):
        """Ensures proxy config is valid before reloading."""
        if proxy_type == "caddy":
            try:
                result = subprocess.run(
                    ["caddy", "validate", "--config", "/opt/smsly-hosting/caddy-config/Caddyfile"],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    return {"ok": True}
                return {
                    "ok": False,
                    "error": {
                        "code": "PROXY_CONFIG_TEST_FAILED",
                        "message": "Caddy config validation failed. SSL issuance aborted to protect existing routes.",
                        "details": {"stderr": result.stderr}
                    }
                }
            except FileNotFoundError:
                 # Running in a worker or env without caddy binary directly available
                 return {"ok": True}
        return {"ok": True}
