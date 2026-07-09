#!/usr/bin/env python3
"""Prometheus exporter: Docker container labels + resource metrics via Docker API.

Handles 500+ containers by collecting resource stats in parallel via a
thread pool.  Falls back to a single-thread on Python builds without
concurrent.futures (cpython-alpine has it).
"""
import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
import traceback

try:
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from concurrent.futures import TimeoutError as FutureTimeout
    _HAS_POOL = True
except ImportError:  # minimal Python builds
    _HAS_POOL = False

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9234"))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "30"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "12"))
STATS_TIMEOUT = int(os.environ.get("STATS_TIMEOUT", "25"))
NODE_NAME = os.environ.get("NODE_NAME", "local")

_metrics_cache = {"data": "", "ts": 0, "container_count": 0}
_lock = threading.Lock()
_collecting = threading.Event()
_cycle_count = 0


def _query_docker(path):
    """Raw HTTP/1.0 GET against the Docker Unix socket.  Returns parsed JSON."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect(DOCKER_SOCK)
        sock.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        body = data.split(b"\r\n\r\n", 1)[1]
        return json.loads(body)
    finally:
        sock.close()


def _extract_container_info(container):
    """Pull label + ID info for a single container dict."""
    name = container.get("Name", "").lstrip("/")
    labels = container.get("Labels", {}) or {}
    cid = container.get("Id", "")
    service_name = labels.get("smsly.blue_green.canonical_name",
                               labels.get("com.docker.compose.service", name))
    base_labels = (
        f'docker_id="{cid}",'
        f'container_name="{name}",'
        f'service_name="{service_name}",'
        f'node="{NODE_NAME}"'
    )
    return name, service_name, cid, base_labels


def _fetch_stats(cid, base_labels):
    """Query /containers/{cid}/stats (stream=false) and return metric lines."""
    try:
        stats = _query_docker(f"/containers/{cid}/stats?stream=false")
    except Exception:
        return []

    cpu = stats.get("cpu_stats", {})
    precpu = stats.get("precpu_stats", {})
    mem = stats.get("memory_stats", {})
    nets = stats.get("networks", {})
    blkio = stats.get("blkio_stats", {}).get("io_service_bytes_recursive", []) or []

    cpu_delta = cpu.get("cpu_usage", {}).get("total_usage", 0) - \
                precpu.get("cpu_usage", {}).get("total_usage", 0)
    system_delta = cpu.get("system_cpu_usage", 0) - \
                   precpu.get("system_cpu_usage", 0)
    num_cpus = cpu.get("online_cpus", 1)
    cpu_usage = (cpu_delta / system_delta * num_cpus) if system_delta > 0 else 0

    mem_usage = mem.get("usage", 0) - \
                mem.get("stats", {}).get("inactive_file", 0)

    net_rx = sum(v.get("rx_bytes", 0) for v in nets.values())
    net_tx = sum(v.get("tx_bytes", 0) for v in nets.values())

    disk_read = sum(e.get("value", 0) for e in blkio if e.get("op") == "read")
    disk_write = sum(e.get("value", 0) for e in blkio if e.get("op") == "write")

    return [
        f'docker_container_cpu_usage_seconds_total{{{base_labels}}} {cpu_usage:.6f}',
        f'docker_container_memory_usage_bytes{{{base_labels}}} {mem_usage}',
        f'docker_container_network_receive_bytes_total{{{base_labels}}} {net_rx}',
        f'docker_container_network_transmit_bytes_total{{{base_labels}}} {net_tx}',
        f'docker_container_fs_reads_bytes_total{{{base_labels}}} {disk_read}',
        f'docker_container_fs_writes_bytes_total{{{base_labels}}} {disk_write}',
    ]


def _collect_serial(managed):
    """Original sequential path — used when concurrent.futures is absent."""
    lines = []
    for _, _, cid, base_labels in managed:
        lines.extend(_fetch_stats(cid, base_labels))
    return lines


def _collect_parallel(managed):
    """Parallel path — up to MAX_WORKERS concurrent stats calls."""
    lines = []
    futures_map = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for _, _, cid, base_labels in managed:
            future = executor.submit(_fetch_stats, cid, base_labels)
            futures_map[future] = (cid, base_labels)
        try:
            for future in as_completed(futures_map, timeout=STATS_TIMEOUT):
                try:
                    lines.extend(future.result())
                except Exception:
                    pass  # container may have disappeared during fetch
        except FutureTimeout:
            elapsed = "?"
            remaining = sum(1 for f in futures_map if not f.done())
            traceback.print_exc(file=sys.stdout)
            print(f"[drain] Stats cycle timed out after {STATS_TIMEOUT}s — "
                  f"{remaining} container(s) pending (cycle={_cycle_count})", flush=True)
    return lines


def _collect_metrics():
    """Enumerate containers, build label metrics, then collect resource stats."""
    global _cycle_count
    _cycle_count += 1
    start = time.time()

    try:
        all_containers = _query_docker("/containers/json?all=true")
    except Exception:
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        return ""

    # ── header ──────────────────────────────────────────────────────────
    out = [
        "# HELP docker_container_labels Docker container labels",
        "# TYPE docker_container_labels gauge",
        "# HELP docker_container_cpu_usage_seconds_total Cumulative CPU usage",
        "# TYPE docker_container_cpu_usage_seconds_total counter",
        "# HELP docker_container_memory_usage_bytes Current memory usage in bytes",
        "# TYPE docker_container_memory_usage_bytes gauge",
        "# HELP docker_container_network_receive_bytes_total Network bytes received",
        "# TYPE docker_container_network_receive_bytes_total counter",
        "# HELP docker_container_network_transmit_bytes_total Network bytes transmitted",
        "# TYPE docker_container_network_transmit_bytes_total counter",
        "# HELP docker_container_fs_reads_bytes_total Cumulative filesystem bytes read",
        "# TYPE docker_container_fs_reads_bytes_total counter",
        "# HELP docker_container_fs_writes_bytes_total Cumulative filesystem bytes written",
        "# TYPE docker_container_fs_writes_bytes_total counter",
    ]

    # ── filter + label metrics ─────────────────────────────────────────
    managed = []
    for c in all_containers:
        labels = c.get("Labels", {}) or {}
        if labels.get("managed_by") != "smsly-hosting":
            continue
        info = _extract_container_info(c)
        managed.append(info)
        out.append(f'docker_container_labels{{{info[3]}}} 1')

    if not managed:
        out.append("")
        return "\n".join(out)

    # ── resource stats ─────────────────────────────────────────────────
    if _HAS_POOL and len(managed) > 1:
        stats_lines = _collect_parallel(managed)
    else:
        stats_lines = _collect_serial(managed)

    out.extend(stats_lines)

    elapsed = time.time() - start
    if elapsed > (REFRESH_INTERVAL * 0.8):
        print(f"[warn] Cycle {_cycle_count} took {elapsed:.1f}s "
              f"(>80% of refresh interval {REFRESH_INTERVAL}s) — "
              f"{len(managed)} containers", flush=True)
    elif elapsed > (REFRESH_INTERVAL * 0.5):
        print(f"[info] Cycle {_cycle_count} took {elapsed:.1f}s "
              f"({len(managed)} containers)", flush=True)

    out.append("")
    return "\n".join(out)


def _background_collector():
    """Infinite loop: collect metrics on REFRESH_INTERVAL cadence with backpressure."""
    while True:
        try:
            if _collecting.is_set():
                print("[skip] Previous collection cycle still in progress — skipping tick", flush=True)
                time.sleep(REFRESH_INTERVAL)
                continue

            _collecting.set()
            data = _collect_metrics()
            with _lock:
                _metrics_cache["data"] = data
                _metrics_cache["ts"] = time.time()
                _metrics_cache["container_count"] = data.count('\n') if data else 0
        except Exception:
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
        finally:
            _collecting.clear()

        time.sleep(REFRESH_INTERVAL)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            ts = _metrics_cache.get("ts", 0)
            count = _metrics_cache.get("container_count", 0)
            body = f"OK container_count={count} last_update={int(ts)}\n".encode()

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return

        with _lock:
            data = _metrics_cache["data"]

        body = data.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Handle each request in a new thread so health checks are never
    blocked by a slow /metrics write."""
    daemon_threads = True


if __name__ == "__main__":
    t = threading.Thread(target=_background_collector, daemon=True)
    t.start()
    print(f"Docker exporter listening on :{LISTEN_PORT} "
          f"(workers={MAX_WORKERS}, refresh={REFRESH_INTERVAL}s, "
          f"parallel={'yes' if _HAS_POOL else 'no'})", flush=True)
    # HTTP server with auto-restart on failure (port conflict, OOM, etc.)
    while True:
        try:
            server = _ThreadedHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
            server.serve_forever()
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except Exception:
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
            time.sleep(5)
