#!/usr/bin/env python3
"""Tiny Prometheus exporter that exposes Docker container labels as metrics."""
import http.server
import json
import os
import socket

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9234"))


def _query_docker(path):
    """Query Docker API via Unix socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
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


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return

        try:
            containers = _query_docker("/containers/json?all=true")
        except Exception as exc:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Error: {exc}".encode())
            return

        lines = [
            "# HELP docker_container_labels Docker container labels as metrics",
            "# TYPE docker_container_labels gauge",
        ]

        for c in containers:
            name = c.get("Name", "").lstrip("/")
            labels = c.get("Labels", {}) or {}
            managed_by = labels.get("managed_by", "")

            if managed_by != "smsly-hosting":
                continue

            service_name = labels.get("smsly.blue_green.canonical_name", name)
            container_id = c.get("Id", "")[:12]

            label_pairs = (
                f'docker_id="{container_id}",'
                f'container_name="{name}",'
                f'service_name="{service_name}",'
                f'managed_by="{managed_by}"'
            )
            lines.append(f'docker_container_labels{{{label_pairs}}} 1')

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write("\n".join(lines).encode())

    def log_message(self, format, *args):
        pass  # suppress access logs


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"Docker labels exporter listening on :{LISTEN_PORT}")
    server.serve_forever()
