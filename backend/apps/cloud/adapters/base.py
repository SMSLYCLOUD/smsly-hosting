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

    # --- Instance Lifecycle (Extended for FVM + Docker unification) ---
    def create_instance(self, name: str, image: str, env: Dict[str, str], resources: Dict[str, int], volumes: List[Dict], network: str, labels: Dict[str, str], healthcheck: Dict) -> str:
        """Create a compute instance (container or microVM). Returns instance_id."""
        raise NotImplementedError

    def start_instance(self, instance_id: str) -> None:
        """Start a created instance."""
        raise NotImplementedError

    def stop_instance(self, instance_id: str, timeout: int = 10) -> None:
        """Stop an instance gracefully."""
        raise NotImplementedError

    def remove_instance(self, instance_id: str, force: bool = False) -> None:
        """Remove an instance."""
        raise NotImplementedError

    def get_instance(self, instance_id: str) -> Dict[str, Any]:
        """Get info about an instance."""
        raise NotImplementedError

    def get_instance_logs(self, instance_id: str, tail: int = 200) -> str:
        """Fetch logs for an instance."""
        raise NotImplementedError

    def wait_instance_healthy(self, instance_id: str, timeout: int = 60) -> bool:
        """Wait until instance passes health checks."""
        raise NotImplementedError

    def exec_in_instance(self, instance_id: str, cmd: str) -> tuple[int, str, str]:
        """Execute command in instance. Returns (exit_code, stdout, stderr)."""
        raise NotImplementedError

    def get_instance_stats(self, instance_id: str) -> Dict[str, Any]:
        """Fetch resource usage metrics for the instance."""
        raise NotImplementedError

    # --- Image Management ---
    def pull_image(self, image: str) -> bool:
        """
        Pull a container image from a registry.
        Returns True if successful.
        """
        return True

    def push_image(self, image: str) -> bool:
        """Push an image to registry."""
        raise NotImplementedError

    def commit_instance(self, instance_id: str) -> str:
        """Commit an instance to an image. Returns image_ref."""
        raise NotImplementedError

    def save_image(self, image_ref: str, path: str) -> None:
        """Save an image to a tar/ext4 file."""
        raise NotImplementedError

    def load_image(self, path: str) -> str:
        """Load an image from a tar/ext4 file. Returns image_ref."""
        raise NotImplementedError

    # --- Volume Management ---
    def create_volume(self, name: str, size: int = 0) -> str:
        """Create a storage volume."""
        raise NotImplementedError

    def remove_volume(self, name: str) -> None:
        """Remove a storage volume."""
        raise NotImplementedError

    # --- Network Management ---
    def create_network(self, name: str, driver: str = "bridge") -> str:
        """Create a network."""
        raise NotImplementedError

    def connect_to_network(self, instance_id: str, network: str, aliases: List[str] = None) -> None:
        """Connect an instance to a network."""
        raise NotImplementedError

    @abstractmethod
    def deploy_container(self, service_name: str, image: str,
                         env_vars: Dict[str, str], cpu: int, memory: int,
                         replicas: int = 1, vpa_enabled: bool = True, **kwargs) -> str:
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
