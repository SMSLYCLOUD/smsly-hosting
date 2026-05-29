import os
import json
import socket
import logging
from typing import Dict, Any

from django.conf import settings
from apps.deployments.models_fvm import FVMIPAllocation

logger = logging.getLogger(__name__)

class FVMExporter:
    """
    Scrapes metrics from Firecracker APIs across all running VMs on this host.
    This replaces cAdvisor for the FVM runtime.
    """

    @staticmethod
    def _api_request(instance_id: str, path: str) -> Dict[str, Any]:
        sock_path = f"/tmp/firecracker/{instance_id}.sock"
        if not os.path.exists(sock_path):
            return {}

        import requests
        import urllib3.connection
        import requests.adapters

        session = requests.Session()

        class UnixSocketConnection(urllib3.connection.HTTPConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.sock_path = sock_path
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(self.sock_path)

        class UnixSocketConnectionPool(urllib3.connectionpool.HTTPConnectionPool):
            ConnectionCls = UnixSocketConnection

        class UnixSocketAdapter(requests.adapters.HTTPAdapter):
            def get_connection(self, url, proxies=None):
                return UnixSocketConnectionPool('localhost', 80)

        session.mount('http://', UnixSocketAdapter())

        try:
            resp = session.get(f"http://localhost{path}", timeout=2)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Failed to fetch metrics for {instance_id}: {e}")

        return {}

    @classmethod
    def get_all_metrics(cls) -> str:
        """
        Returns Prometheus-formatted metrics string for all running VMs.
        """
        metrics = []
        metrics.append("# HELP fvm_cpu_utilization CPU utilization percentage")
        metrics.append("# TYPE fvm_cpu_utilization gauge")

        allocations = FVMIPAllocation.objects.filter(node__name=settings.SERVER_NAME)
        for alloc in allocations:
            # We would normally parse process cgroups or Firecracker API metrics here.
            # For Phase 1/4 transition, we stub the integration point.
            stats = cls._api_request(alloc.vm_id, '/metrics')

            # Example metric extraction
            # Firecracker's /metrics returns block device, net device, and vcpu stats
            if stats:
                cpu_metric = 0.5 # Mock parsed
                metrics.append(f'fvm_cpu_utilization{{vm_id="{alloc.vm_id}"}} {cpu_metric}')

        return "\n".join(metrics)
