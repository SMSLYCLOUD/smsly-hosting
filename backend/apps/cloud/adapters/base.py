"""Base module."""
from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseCloudAdapter(ABC):
    """
    Abstract Base Class for all Cloud Providers.
    Defines the contract for Compute, Storage, and Networking operations.
    """

    @abstractmethod
    def authenticate(self) -> bool:
        """Verify credentials are valid."""

    @abstractmethod
    def pull_image(self, image: str) -> bool:
        """
        Pull a container image from a registry.
        Returns True if successful.
        """

    @abstractmethod
    def deploy_container(self, service_name: str, image: str,
                         env_vars: Dict[str, str], cpu: int, memory: int,
                         replicas: int = 1, **kwargs) -> str:
        # pylint: disable=too-many-positional-arguments, too-many-arguments
        """
        Deploy a containerized application.
        Returns the resource ID (ARN, etc).

        Optional kwargs:
            volumes: List[Dict] - [{'name': str, 'mount_path': str}]
            healthcheck: Dict - {'path': str, 'interval': int, 'timeout': int, 'retries': int}
        """

    @abstractmethod
    def deploy_function(self, function_name: str,
                        code_zip: str, handler: str, runtime: str) -> str:
        """
        Deploy a serverless function.
        """

    # --- Storage ---
    @abstractmethod
    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        """Create an object storage bucket."""

    @abstractmethod
    def provision_database(self, db_name: str, engine: str,
                           version: str) -> str:
        """Provision a managed database (RDS/CloudSQL)."""

    # --- Networking ---
    @abstractmethod
    def create_vpc(self, cidr_block: str) -> str:
        """Create a Virtual Private Cloud."""

    @abstractmethod
    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        """Create a WAF (Web Application Firewall) ACL/Policy."""

    @abstractmethod
    def issue_ssl_cert(self, domain_name: str) -> str:
        """Request/Issue an SSL Certificate (ACM/Managed)."""

    # --- Security ---
    @abstractmethod
    def create_iam_role(self, role_name: str, policy: Dict[str, Any]) -> str:
        """Create an IAM Role with specific permissions."""

    @abstractmethod
    def store_secret(self, secret_name: str, secret_value: str) -> str:
        """Store a secret in the provider's secret manager."""

    # --- Observability ---
    @abstractmethod
    def get_metrics(self, resource_id: str, metric_name: str,
                    start_time: str, end_time: str) -> List[Dict]:
        """Fetch metrics for a resource."""
