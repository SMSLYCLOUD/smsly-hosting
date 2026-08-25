"""Netcup adapter — CCP API (api.netcup.de) + SCP vServer API."""
import logging
import requests

from .base import BaseCloudAdapter

logger = logging.getLogger(__name__)


class NetcupAdapter(BaseCloudAdapter):
    API_BASE = "https://api.netcup.de"

    def __init__(self, api_key: str, api_password: str, customer_number: str = "", **kwargs):
        # Netcup uses: loginName (customer number), password, apikey + apisession
        # We expose api_key = apikey, api_secret = apipassword, project_id = customer_number for flexibility
        self.api_key = api_key
        self.api_password = api_password
        self.customer_number = customer_number or kwargs.get("project_id") or ""

    def _login(self) -> str | None:
        try:
            resp = requests.post(f"{self.API_BASE}", json={
                "action": "login",
                "param": {
                    "customernumber": self.customer_number,
                    "apikey": self.api_key,
                    "apipassword": self.api_password,
                }
            }, timeout=10)
            data = resp.json()
            if data.get("status") == "success":
                return data["responsedata"].get("apisessionid")
        except Exception as e:
            logger.debug("Netcup login failed: %s", e)
        return None

    def authenticate(self) -> bool:
        return self._login() is not None

    def deploy_container(self, service_name: str, image: str, env_vars: dict, cpu: int, memory: int, replicas: int = 1, **kwargs) -> str:
        raise NotImplementedError("Netcup vServer deploy — use LOCAL/REMOTE adapter for PaaS; Netcup API provisions raw VMs")

    def deploy_function(self, *a, **kw) -> str:
        raise NotImplementedError("Netcup has no serverless")

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        # Netcup Object Storage (S3-compatible via netcup S3)
        return f"netcup-obj:{bucket_name}"

    def provision_database(self, *a, **kw) -> str:
        return "netcup-db:self-hosted"

    def create_vpc(self, cidr_block: str) -> str:
        return f"netcup-vpc:{cidr_block}"

    def create_waf_policy(self, *a, **kw) -> str:
        return "netcup-no-waf"

    def issue_ssl_cert(self, domain_name: str) -> str:
        return f"letsencrypt:{domain_name}"

    def create_iam_role(self, *a, **kw) -> str:
        return "netcup-no-iam"

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        return f"platform-secret:{secret_name}"

    def get_metrics(self, *a, **kw) -> list[dict]:
        return []
