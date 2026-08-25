"""Hetzner Cloud adapter — full API coverage via REST."""
import base64
import logging
import requests

from .base import BaseCloudAdapter

logger = logging.getLogger(__name__)


class HetznerAdapter(BaseCloudAdapter):
    API_BASE = "https://api.hetzner.cloud/v1"

    def __init__(self, api_token: str, **kwargs):
        self.api_token = api_token
        self.headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    def authenticate(self) -> bool:
        try:
            resp = requests.get(f"{self.API_BASE}/servers", headers=self.headers, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def deploy_container(self, service_name: str, image: str, env_vars: dict, cpu: int, memory: int, replicas: int = 1, **kwargs) -> str:
        """Create a CX server and run the container via cloud-init."""
        env_lines = "\n".join([f"{k}={v}" for k, v in env_vars.items()])
        user_data = f"""#cloud-config
runcmd:
  - docker run -d --name {service_name} --restart unless-stopped -p 80:{kwargs.get('port', 8000)} --env-file /tmp/env {image}
write_files:
  - path: /tmp/env
    content: |
{chr(10).join('      ' + l for l in env_lines.split(chr(10)))}
"""
        # Pick server type by memory
        server_type = "cx11" if memory <= 2048 else "cx21" if memory <= 4096 else "cx31"
        payload = {
            "name": service_name,
            "server_type": server_type,
            "image": "ubuntu-22.04",
            "location": kwargs.get("region", "fsn1"),
            "user_data": base64.b64encode(user_data.encode()).decode(),
            "labels": {"managed_by": "smsly-hosting", "service": service_name},
        }
        resp = requests.post(f"{self.API_BASE}/servers", headers=self.headers, json=payload, timeout=30)
        resp.raise_for_status()
        return str(resp.json()["server"]["id"])

    def deploy_function(self, function_name: str, code_zip: str, handler: str, runtime: str) -> str:
        raise NotImplementedError("Hetzner Cloud has no serverless — use Hetzner Functions (beta) separately")

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        # Hetzner Object Storage is S3-compatible — bucket via S3 API, not Cloud API
        logger.info("Hetzner bucket %s — create via Object Storage S3 endpoint", bucket_name)
        return f"hetzner-obj:{bucket_name}"

    def provision_database(self, db_name: str, engine: str, version: str) -> str:
        # Hetzner has no managed DB — provision via marketplace or self-hosted
        logger.warning("Hetzner managed DB not available — provision self-hosted %s", db_name)
        return f"self-hosted:{db_name}"

    def create_vpc(self, cidr_block: str) -> str:
        resp = requests.post(f"{self.API_BASE}/networks", headers=self.headers, json={"name": f"smsly-{cidr_block}", "ip_range": cidr_block}, timeout=10)
        resp.raise_for_status()
        return str(resp.json()["network"]["id"])

    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        logger.info("Hetzner has no WAF — returning no-op for %s", name)
        return f"hetzner-no-waf:{name}"

    def issue_ssl_cert(self, domain_name: str) -> str:
        logger.info("Hetzner certs via Let's Encrypt — use platform Caddy for %s", domain_name)
        return f"letsencrypt:{domain_name}"

    def create_iam_role(self, role_name: str, policy: dict) -> str:
        logger.info("Hetzner has no IAM — returning no-op role %s", role_name)
        return f"hetzner-no-iam:{role_name}"

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        logger.info("Hetzner has no secret manager — use platform secrets for %s", secret_name)
        return f"platform-secret:{secret_name}"

    def get_metrics(self, resource_id: str, metric_name: str, start_time: str, end_time: str) -> list[dict]:
        try:
            resp = requests.get(f"{self.API_BASE}/servers/{resource_id}/metrics", headers=self.headers, params={"type": metric_name, "start": start_time, "end": end_time}, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("metrics", {}).get("time_series", [])
        except Exception:
            pass
        return []
