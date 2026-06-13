"""
Container-level metrics collection.

Replaces the inline ``_docker_stats_legacy`` / ``_k8s_stats`` /
``_parse_mem`` / ``_parse_k8s_cpu`` / ``_parse_k8s_memory`` helpers
that previously lived in ``apps.autoscaler.views``. They are still
used by the container-level autoscaler dashboard (which scales
*platform* containers like celery/gunicorn, not user-deployed
``Service`` rows) and have been moved here so they can be shared
between the per-service pipeline and the per-container dashboard.
"""
import json
import logging
import os
import socket
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


# K8s availability flag — populated lazily by ``init_k8s``
K8S_AVAILABLE = False
_k8s_clients: dict = {}


def init_k8s() -> bool:
    """Try to load an in-cluster or kubeconfig K8s client. Returns True on success."""
    global K8S_AVAILABLE
    if _k8s_clients.get('available') is not None:
        return _k8s_clients['available']
    try:
        from kubernetes import client as k8s_client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except BaseException:
            k8s_config.load_kube_config()
        _k8s_clients['client'] = k8s_client
        _k8s_clients['available'] = True
        K8S_AVAILABLE = True
    except Exception:
        _k8s_clients['available'] = False
        K8S_AVAILABLE = False
    return K8S_AVAILABLE


def k8s_available() -> bool:
    return init_k8s()


def collect_container_stats() -> dict:
    """Collect container-level metrics. Returns ``{container_name: stats_dict}``."""
    if k8s_available():
        result = _k8s_container_stats()
        if result is not None:
            return result
    return docker_stats_legacy()


def _k8s_container_stats() -> Optional[dict]:
    try:
        from kubernetes import client as k8s_client
        metrics_api = k8s_client.CustomObjectsApi()
        pod_metrics = metrics_api.list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="pods"
        )
        containers = {}
        for pod in pod_metrics.get("items", []):
            name = pod["metadata"]["name"]
            total_cpu = 0
            total_mem = 0
            for container in pod.get("containers", []):
                cpu_raw = container["usage"].get("cpu", "0")
                mem_raw = container["usage"].get("memory", "0")
                total_cpu += parse_k8s_cpu(cpu_raw)
                total_mem += parse_k8s_memory(mem_raw)
            containers[name] = {
                "cpu_percent": round(total_cpu, 1),
                "memory_mb": round(total_mem, 1),
                "memory_limit_mb": 0,
                "memory_percent": 0,
                "net_rx_mb": 0,
                "net_tx_mb": 0,
                "pids": 0,
            }
        return containers
    except Exception as exc:
        logger.warning("K8s metrics API unavailable, falling back to docker stats: %s", exc)
        return None


def parse_k8s_cpu(cpu_str: str) -> float:
    """Parse K8s CPU string (e.g. '100m', '1') to millicores float."""
    cpu_str = cpu_str.strip()
    if cpu_str.endswith("m"):
        return float(cpu_str[:-1])
    return float(cpu_str) * 1000


def parse_k8s_memory(mem_str: str) -> float:
    """Parse K8s memory string (e.g. '128Mi', '1Gi', '512Ki') to MiB."""
    mem_str = mem_str.strip()
    if mem_str.endswith("Ki"):
        return float(mem_str[:-2]) / 1024
    if mem_str.endswith("Mi"):
        return float(mem_str[:-2])
    if mem_str.endswith("Gi"):
        return float(mem_str[:-2]) * 1024
    if mem_str.endswith("Ti"):
        return float(mem_str[:-2]) * 1024 * 1024
    return float(mem_str) / (1024 * 1024)


def docker_stats_legacy() -> dict:
    """Collect container metrics via ``docker stats --no-stream``."""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}"],
            capture_output=True, text=True, timeout=15,
        )
        containers = {}
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            name, cpu_str, mem_usage, mem_pct, net_io, pids = parts
            mem_parts = mem_usage.split("/")
            mem_used_mb = parse_mem(mem_parts[0].strip()) if len(mem_parts) >= 1 else 0
            mem_limit_mb = parse_mem(mem_parts[1].strip()) if len(mem_parts) >= 2 else 0
            net_parts = net_io.split("/")
            net_rx = parse_mem(net_parts[0].strip()) if len(net_parts) >= 1 else 0
            net_tx = parse_mem(net_parts[1].strip()) if len(net_parts) >= 2 else 0

            containers[name] = {
                "cpu_percent": _safe_float(cpu_str.replace('%', '')),
                "memory_mb": round(mem_used_mb, 1),
                "memory_limit_mb": round(mem_limit_mb, 1),
                "memory_percent": _safe_float(mem_pct.replace('%', '')),
                "net_rx_mb": round(net_rx, 2),
                "net_tx_mb": round(net_tx, 2),
                "pids": int(pids) if pids.isdigit() else 0,
            }
        return containers
    except Exception as exc:
        logger.error("Failed to get docker stats: %s", exc)
        return {}


def parse_mem(s: str) -> float:
    """Convert strings like '123.4MiB' or '1.5GiB' to megabytes (float)."""
    s = s.strip()
    try:
        if "GiB" in s or "GB" in s:
            return float(s.replace("GiB", "").replace("GB", "").strip()) * 1024
        if "MiB" in s or "MB" in s:
            return float(s.replace("MiB", "").replace("MB", "").strip())
        if "KiB" in s or "kB" in s or "KB" in s:
            return float(s.replace("KiB", "").replace("kB", "").replace("KB", "").strip()) / 1024
        if "B" in s:
            return float(s.replace("B", "").strip()) / (1024 * 1024)
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _safe_float(v: str) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0
