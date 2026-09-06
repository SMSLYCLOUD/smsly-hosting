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
import concurrent.futures
import shutil
import subprocess
import threading

logger = logging.getLogger(__name__)

# Per-container stats timeout (seconds). Docker SDK has no built-in
# per-request timeout for stats(), so we wrap in a thread.
STATS_PER_CONTAINER_TIMEOUT = 3
# Overall timeout for collecting ALL container stats (seconds).
STATS_OVERALL_TIMEOUT = 20
# `docker stats --no-stream` is ONE daemon round-trip for every
# container (~2s total on 60+ containers) vs 2s PER container via
# docker-py stats(stream=False) — the SDK path took 130s+ on busy
# hosts and blew the dashboard's 15s API budget, leaving the
# Autoscaler tab permanently empty.
STATS_CLI_TIMEOUT = 12

# Lazy-loaded K8s clients — only imported when the kubernetes package
# is available.  ``init_k8s()`` populates ``_k8s_clients`` on first use.
k8s_config = None
k8s_client = None
try:
    from kubernetes import client as _k8s_client
    from kubernetes import config as _k8s_config
    k8s_config = _k8s_config
    k8s_client = _k8s_client
except ImportError:
    pass


# K8s availability flag — populated lazily by ``init_k8s``
K8S_AVAILABLE = False
_k8s_clients: dict = {}


def init_k8s() -> bool:
    """Try to load an in-cluster or kubeconfig K8s client. Returns True on success."""
    global K8S_AVAILABLE
    if _k8s_clients.get('available') is not None:
        return _k8s_clients['available']
    if k8s_config is None or k8s_client is None:
        _k8s_clients['available'] = False
        K8S_AVAILABLE = False
        return False
    try:
        try:
            k8s_config.load_incluster_config()
        except Exception:
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
    """Collect container-level metrics. Returns ``{container_name: stats_dict}``.

    Priority: docker CLI bulk stats (one daemon round-trip, ~2s for
    60+ containers) -> K8s metrics API -> per-container SDK fallback.
    """
    cli_stats = _docker_stats_cli()
    if cli_stats is not None:
        return cli_stats

    if k8s_available():
        result = _k8s_container_stats()
        if result is not None:
            return result
    return docker_stats_legacy()


def _k8s_container_stats() -> dict | None:
    try:
        metrics_api = k8s_client.CustomObjectsApi()
        pod_metrics = metrics_api.list_cluster_custom_object(
            group="metrics.k8s.io", version="v1beta1", plural="pods"
        )
        containers = {}
        for pod in pod_metrics.get("items", []):
            name = pod["metadata"]["name"]
            total_cpu: float = 0.0
            total_mem: float = 0.0
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


def _docker_stats_cli() -> dict | None:
    """Collect ALL container stats in ONE daemon round-trip via
    ``docker stats --no-stream --format json``.

    Returns None when the docker CLI is unavailable or the call fails
    (callers fall back to the per-container SDK path).
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return None
    try:
        result = subprocess.run(
            [docker_bin, "stats", "--no-stream", "--format", "json"],
            capture_output=True, text=True, timeout=STATS_CLI_TIMEOUT,
        )
        if result.returncode != 0:
            logger.debug("docker stats CLI rc=%d: %s",
                         result.returncode, (result.stderr or '')[:200])
            return None

        containers: dict = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("Name") or row.get("Container")
            if not name:
                continue
            containers[name] = {
                "cpu_percent": _safe_float(row.get("CPUPercentage", "0")),
                "memory_mb": parse_mem(row.get("MemUsage", "0") .split("/")[0].strip()),
                "memory_limit_mb": parse_mem((row.get("MemUsage") or "0/0").split("/")[-1].strip()),
                "memory_percent": _safe_float(row.get("MemPerc", "0")),
                "net_rx_mb": parse_mem(row.get("NetIO", "0 / 0").split("/")[0].strip()),
                "net_tx_mb": parse_mem((row.get("NetIO") or "0 / 0").split("/")[-1].strip()),
                "pids": int(_safe_float(row.get("PIDs", "0"))),
            }
        return containers if containers else None
    except subprocess.TimeoutExpired:
        logger.warning("docker stats CLI timed out after %ds", STATS_CLI_TIMEOUT)
        return None
    except Exception as exc:
        logger.debug("docker stats CLI failed: %s", exc)
        return None


def docker_stats_legacy() -> dict:
    """Collect container metrics via the Docker SDK (docker-py).

    Uses parallel stats collection with per-container and overall timeouts
    to prevent the API from hanging when Docker is slow.
    """
    try:
        from apps.cloud.docker_client import get_docker_client

        client = get_docker_client(timeout=5)
        container_list = client.containers.list()

        def _collect_one(container):
            result = [None]
            def _do_collect():
                try:
                    stats = container.stats(stream=False)
                except Exception:
                    return

                cpu_stats = stats.get("cpu_stats", {}) or {}
                precpu_stats = stats.get("precpu_stats", {}) or {}
                cpu_usage = cpu_stats.get("cpu_usage", {}) or {}
                precpu_usage = precpu_stats.get("cpu_usage", {}) or {}
                cpu_delta = cpu_usage.get("total_usage", 0) - precpu_usage.get("total_usage", 0)
                system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
                num_cpus = cpu_stats.get("online_cpus") or 1
                if system_delta > 0 and cpu_delta >= 0:
                    cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0
                else:
                    cpu_percent = 0.0

                mem = stats.get("memory_stats", {}) or {}
                mem_used = mem.get("usage", 0) or 0
                mem_limit = mem.get("limit", 0) or 0
                mem_used_mb = mem_used / (1024 * 1024)
                mem_limit_mb = mem_limit / (1024 * 1024) if mem_limit else 0.0
                mem_percent = (mem_used / mem_limit * 100.0) if mem_limit else 0.0

                networks = stats.get("networks", {}) or {}
                rx_bytes = sum(n.get("rx_bytes", 0) for n in networks.values())
                tx_bytes = sum(n.get("tx_bytes", 0) for n in networks.values())

                pids = (stats.get("pids_stats", {}) or {}).get("current", 0) or 0

                result[0] = (container.name, {
                    "cpu_percent": round(cpu_percent, 1),
                    "memory_mb": round(mem_used_mb, 1),
                    "memory_limit_mb": round(mem_limit_mb, 1),
                    "memory_percent": round(mem_percent, 1),
                    "net_rx_mb": round(rx_bytes / (1024 * 1024), 2),
                    "net_tx_mb": round(tx_bytes / (1024 * 1024), 2),
                    "pids": int(pids),
                })

            t = threading.Thread(target=_do_collect, daemon=True)
            t.start()
            t.join(timeout=STATS_PER_CONTAINER_TIMEOUT)
            if t.is_alive():
                logger.debug("Stats timeout for %s after %ds", container.name, STATS_PER_CONTAINER_TIMEOUT)
            return result[0]

        containers = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(_collect_one, c): c for c in container_list}
            try:
                for future in concurrent.futures.as_completed(futures, timeout=STATS_OVERALL_TIMEOUT):
                    try:
                        result = future.result(timeout=0)
                        if result is not None:
                            name, data = result
                            containers[name] = data
                    except Exception as exc:
                        logger.debug("Stats collection failed: %s", exc)
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "Stats collection overall timeout after %ds (%d/%d containers collected)",
                    STATS_OVERALL_TIMEOUT, len(containers), len(container_list),
                )

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
