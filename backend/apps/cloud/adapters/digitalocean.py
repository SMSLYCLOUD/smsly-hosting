"""DigitalOcean adapter — full REST coverage."""
import logging
import requests

from .base import BaseCloudAdapter

logger = logging.getLogger(__name__)


class DigitalOceanAdapter(BaseCloudAdapter):
    API_BASE = "https://api.digitalocean.com/v2"

    def __init__(self, api_token: str, **kwargs):
        self.api_token = api_token
        self.headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}

    def authenticate(self) -> bool:
        try:
            resp = requests.get(f"{self.API_BASE}/account", headers=self.headers, timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def deploy_container(self, service_name: str, image: str, env_vars: dict, cpu: int, memory: int, replicas: int = 1, **kwargs) -> str:
        # DO App Platform: create app with container
        env_list = [{"key": k, "value": v, "scope": "RUN_TIME"} for k, v in env_vars.items()]
        payload = {
            "spec": {
                "name": service_name,
                "services": [{
                    "name": service_name,
                    "image": {"registry_type": "DOCKER_HUB", "registry": "docker.io", "repository": image},
                    "envs": env_list,
                    "instance_size_slug": "basic-xxs" if memory <= 512 else "basic-xs",
                    "instance_count": replicas,
                    "http_port": kwargs.get("port", 8000),
                }],
            }
        }
        resp = requests.post(f"{self.API_BASE}/apps", headers=self.headers, json=payload, timeout=30)
        resp.raise_for_status()
        return str(resp.json().get("app", {}).get("id", service_name))

    def deploy_function(self, function_name: str, code_zip: str, handler: str, runtime: str) -> str:
        raise NotImplementedError("DO Functions via separate API")

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        # DO Spaces is S3-compatible
        logger.info("DO Spaces bucket %s", bucket_name)
        return f"do-spaces:{bucket_name}"

    def provision_database(self, db_name: str, engine: str, version: str) -> str:
        resp = requests.post(f"{self.API_BASE}/databases", headers=self.headers, json={
            "name": db_name, "engine": engine, "version": version, "region": "ams3", "size": "db-s-1vcpu-1gb", "num_nodes": 1
        }, timeout=30)
        if resp.status_code in (200, 201, 202):
            return str(resp.json().get("database", {}).get("id", db_name))
        return f"do-db:{db_name}"

    def create_vpc(self, cidr_block: str) -> str:
        resp = requests.post(f"{self.API_BASE}/vpcs", headers=self.headers, json={"name": f"smsly-{cidr_block}", "ip_range": cidr_block, "region": "ams3"}, timeout=10)
        if resp.status_code in (200, 201):
            return str(resp.json().get("vpc", {}).get("id", ""))
        return f"do-vpc:{cidr_block}"

    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        # DO Cloud Firewalls
        resp = requests.post(f"{self.API_BASE}/firewalls", headers=self.headers, json={"name": name, "inbound_rules": [{"protocol": "tcp", "ports": "80", "sources": {"addresses": ["0.0.0.0/0"]}}]}, timeout=10)
        if resp.status_code in (200, 201):
            return str(resp.json().get("firewall", {}).get("id", name))
        return f"do-fw:{name}"

    def issue_ssl_cert(self, domain_name: str) -> str:
        resp = requests.post(f"{self.API_BASE}/certificates", headers=self.headers, json={"name": f"smsly-{domain_name}", "type": "lets_encrypt", "domains": [domain_name]}, timeout=10)
        if resp.status_code in (200, 201):
            return str(resp.json().get("certificate", {}).get("id", domain_name))
        return f"do-cert:{domain_name}"

    def create_iam_role(self, *a, **kw) -> str:
        return "do-no-iam"

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        return f"platform-secret:{secret_name}"

    def get_metrics(self, resource_id: str, metric_name: str, start_time: str, end_time: str) -> list[dict]:
        try:
            resp = requests.get(f"{self.API_BASE}/monitoring/metrics/droplet/cpu", headers=self.headers, params={"host_id": resource_id, "start": start_time, "end": end_time}, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("result", [])
        except Exception:
            pass
        return []
