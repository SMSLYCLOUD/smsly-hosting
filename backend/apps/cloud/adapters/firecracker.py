import json
import logging
import os
import shlex
import socket
import subprocess
import time
from typing import Dict, Any, List

import requests

from .base import BaseCloudAdapter

logger = logging.getLogger(__name__)

class FirecrackerAdapter(BaseCloudAdapter):
    """
    Adapter for Firecracker MicroVMs.
    """
    def __init__(self):
        self.fvm_base_path = "/opt/smsly-hosting/fvm-instances"
        self.kernels_path = "/opt/smsly-hosting/fvm-kernels"
        self.volumes_path = "/opt/smsly-hosting/fvm-volumes"

        os.makedirs(self.fvm_base_path, exist_ok=True)
        os.makedirs(self.kernels_path, exist_ok=True)
        os.makedirs(self.volumes_path, exist_ok=True)

    def authenticate(self) -> bool:
        return True

    def _get_api_socket_path(self, instance_id: str) -> str:
        return f"/tmp/firecracker/{instance_id}.sock"

    def _get_vsock_path(self, instance_id: str) -> str:
        return f"/tmp/firecracker/{instance_id}.vsock"

    def _api_request(self, instance_id: str, method: str, path: str, data: dict = None) -> requests.Response:
        sock_path = self._get_api_socket_path(instance_id)

        # Firecracker uses HTTP over Unix domain sockets
        session = requests.Session()
        import requests
        import urllib3.connection

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

        url = f"http://localhost{path}"
        if method.upper() == 'GET':
            return session.get(url)
        elif method.upper() == 'PUT':
            return session.put(url, json=data)
        elif method.upper() == 'PATCH':
            return session.patch(url, json=data)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

    def create_instance(self, name: str, image: str, env: Dict[str, str], resources: Dict[str, int], volumes: List[Dict], network: str, labels: Dict[str, str], healthcheck: Dict) -> str:
        instance_id = name
        vm_dir = os.path.join(self.fvm_base_path, instance_id)
        os.makedirs(vm_dir, exist_ok=True)

        os.makedirs('/tmp/firecracker', exist_ok=True)
        sock_path = self._get_api_socket_path(instance_id)
        if os.path.exists(sock_path):
            os.remove(sock_path)

        # Start firecracker process in background
        cmd = ["firecracker", "--api-sock", sock_path]
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait for socket
        for _ in range(10):
            if os.path.exists(sock_path):
                break
            time.sleep(0.1)

        # Configure Machine (CPU/Mem)
        vcpu_count = resources.get('cpu', 1)
        mem_size_mib = resources.get('memory', 512)

        self._api_request(instance_id, 'PUT', '/machine-config', {
            "vcpu_count": vcpu_count,
            "mem_size_mib": mem_size_mib,
            "smt": False,
            "track_dirty_pages": True
        })

        # Boot Source (Kernel)
        kernel_path = os.environ.get("FVM_KERNEL_PATH", os.path.join(self.kernels_path, "vmlinux"))

        self._api_request(instance_id, 'PUT', '/boot-source', {
            "kernel_image_path": kernel_path,
            "boot_args": f"console=ttyS0 reboot=k panic=1 pci=off root=/dev/vda rw ip={env.get('FVM_IP', '172.30.0.100')}::172.30.0.1:255.255.0.0::eth0:off"
        })

        # Rootfs Drive
        self._api_request(instance_id, 'PUT', '/drives/rootfs', {
            "drive_id": "rootfs",
            "path_on_host": image,
            "is_root_device": True,
            "is_read_only": False
        })

        # Setup TAP networking
        tap_name = f"tap-{instance_id[:11]}"
        subprocess.run(["ip", "tuntap", "add", "dev", tap_name, "mode", "tap"], check=False)
        subprocess.run(["ip", "link", "set", "dev", tap_name, "up"], check=False)
        subprocess.run(["ip", "link", "set", tap_name, "master", "smsly-fvm"], check=False)

        # Determine MAC from instance ID (simplistic deterministic mapping for now)
        mac_addr = f"AA:FC:00:00:00:{hash(instance_id) % 255:02x}"

        self._api_request(instance_id, 'PUT', '/network-interfaces/eth0', {
            "iface_id": "eth0",
            "guest_mac": mac_addr,
            "host_dev_name": tap_name
        })

        # Setup Vsock
        vsock_path = self._get_vsock_path(instance_id)
        if os.path.exists(vsock_path):
            os.remove(vsock_path)

        self._api_request(instance_id, 'PUT', '/vsock', {
            "guest_cid": hash(instance_id) % 4000000000 + 3,
            "uds_path": vsock_path
        })

        # Setup Logger
        log_dir = f"/var/log/smsly-fvm/{instance_id}"
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "console.log")
        open(log_file, 'a').close()

        self._api_request(instance_id, 'PUT', '/logger', {
            "log_path": log_file,
            "level": "Info",
            "show_level": True,
            "show_log_origin": True
        })

        return instance_id

    def start_instance(self, instance_id: str) -> None:
        self._api_request(instance_id, 'PUT', '/actions', {
            "action_type": "InstanceStart"
        })

    def stop_instance(self, instance_id: str, timeout: int = 10) -> None:
        try:
            self._api_request(instance_id, 'PUT', '/actions', {
                "action_type": "SendCtrlAltDel"
            })
        except Exception:
            pass

    def remove_instance(self, instance_id: str, force: bool = False) -> None:
        try:
            self.stop_instance(instance_id)
        except Exception:
            pass

        sock_path = self._get_api_socket_path(instance_id)
        if os.path.exists(sock_path):
            os.remove(sock_path)

        tap_name = f"tap-{instance_id[:11]}"
        subprocess.run(["ip", "link", "delete", tap_name], check=False)

    def get_instance(self, instance_id: str) -> Dict[str, Any]:
        resp = self._api_request(instance_id, 'GET', '/machine-config')
        return resp.json()

    def get_instance_logs(self, instance_id: str, tail: int = 200) -> str:
        log_file = f"/var/log/smsly-fvm/{instance_id}/console.log"
        if not os.path.exists(log_file):
            return ""
        try:
            output = subprocess.check_output(["tail", f"-n{tail}", log_file])
            return output.decode('utf-8', errors='replace')
        except Exception:
            return ""

    def wait_instance_healthy(self, instance_id: str, timeout: int = 60) -> bool:
        # TODO: Implement host-based HTTP/TCP healthchecks via VM IP
        time.sleep(5)
        return True

    def exec_in_instance(self, instance_id: str, cmd: str) -> tuple[int, str, str]:
        # TODO: Implement vsock-based exec
        return (0, "", "")

    def get_instance_stats(self, instance_id: str) -> Dict[str, Any]:
        return {}

    def deploy_container(self, service_name: str, image: str,
                         env_vars: Dict[str, str], cpu: int, memory: int,
                         replicas: int = 1, vpa_enabled: bool = True, **kwargs) -> str:
        raise NotImplementedError("Use create_instance for FVM")

    def deploy_function(self, function_name: str,
                        code_zip: str, handler: str, runtime: str) -> str:
        raise NotImplementedError

    def create_bucket(self, bucket_name: str, public: bool = False) -> str:
        raise NotImplementedError

    def provision_database(self, db_name: str, engine: str, version: str) -> str:
        raise NotImplementedError

    def create_vpc(self, cidr_block: str) -> str:
        raise NotImplementedError

    def create_waf_policy(self, name: str, scope: str = 'REGIONAL') -> str:
        raise NotImplementedError

    def issue_ssl_cert(self, domain_name: str) -> str:
        raise NotImplementedError

    def create_iam_role(self, role_name: str, policy: Dict[str, Any]) -> str:
        raise NotImplementedError

    def store_secret(self, secret_name: str, secret_value: str) -> str:
        raise NotImplementedError

    def get_metrics(self, resource_id: str, metric_name: str, start_time: str, end_time: str) -> List[Dict]:
        raise NotImplementedError
