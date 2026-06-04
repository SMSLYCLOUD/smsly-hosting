#!/usr/bin/env python3
"""Prometheus exporter: Docker container labels + resource metrics via Docker API."""
import http.server
import json
import os
import socket
import sys
import time
import threading
import traceback

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9234"))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "10"))

_metrics_cache = {"data": "", "ts": 0}
_lock = threading.Lock()


def _query_docker(path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(DOCKER_SOCK)
    sock.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    sock.close()
    body = data.split(b"\r\n\r\n", 1)[1]
    return json.loads(body)


def _collect_metrics():
    try:
        containers = _query_docker("/containers/json?all=true")
    except Exception:
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        return ""

    lines = [
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
    ]

    for c in containers:
        name = c.get("Name", "").lstrip("/")
        labels = c.get("Labels", {}) or {}
        managed_by = labels.get("managed_by", "")

        if managed_by != "smsly-hosting":
            continue

        service_name = labels.get("smsly.blue_green.canonical_name", name)
        cid = c.get("Id", "")

        base_labels = (
            f'docker_id="{cid}",'
            f'container_name="{name}",'
            f'service_name="{service_name}"'
        )

        lines.append(f'docker_container_labels{{{base_labels}}} 1')

        try:
            stats = _query_docker(f"/containers/{cid}/stats?stream=false")
        except Exception:
            continue

        cpu_delta = stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0) - \
                    stats.get("precpu_stats", {}).get("cpu_usage", {}).get("total_usage", 0)
        system_delta = stats.get("cpu_stats", {}).get("system_cpu_usage", 0) - \
                       stats.get("precpu_stats", {}).get("system_cpu_usage", 0)
        num_cpus = stats.get("cpu_stats", {}).get("online_cpus", 1)
        cpu_usage = (cpu_delta / system_delta * num_cpus) if system_delta > 0 else 0

        mem_usage = stats.get("memory_stats", {}).get("usage", 0) - \
                    stats.get("memory_stats", {}).get("stats", {}).get("inactive_file", 0)

        net_rx = sum(v.get("rx_bytes", 0) for v in stats.get("networks", {}).values())
        net_tx = sum(v.get("tx_bytes", 0) for v in stats.get("networks", {}).values())

        lines.append(f'docker_container_cpu_usage_seconds_total{{{base_labels}}} {cpu_usage:.6f}')
        lines.append(f'docker_container_memory_usage_bytes{{{base_labels}}} {mem_usage}')
        lines.append(f'docker_container_network_receive_bytes_total{{{base_labels}}} {net_rx}')
        lines.append(f'docker_container_network_transmit_bytes_total{{{base_labels}}} {net_tx}')

    return "\n".join(lines)


def _background_collector():
    while True:
        try:
            data = _collect_metrics()
            with _lock:
                _metrics_cache["data"] = data
                _metrics_cache["ts"] = time.time()
        except Exception:
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
        time.sleep(REFRESH_INTERVAL)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return

        with _lock:
            data = _metrics_cache["data"]

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(data.encode())

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    try:
        t = threading.Thread(target=_background_collector, daemon=True)
        t.start()
        print(f"Docker exporter listening on :{LISTEN_PORT}", flush=True)
        server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
        server.serve_forever()
    except Exception:
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
