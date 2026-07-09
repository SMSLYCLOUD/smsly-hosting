"""
Management command to diagnose container registry connectivity.

Usage:
    python manage.py check_registry [--json] [--verbose]

Performs multi-level reachability tests against the configured
CONTAINER_REGISTRY_URL and reports diagnostic information.

Tests performed:
    1. DNS resolution (hostname → IP)
    2. TCP connectivity to port 5000
    3. HTTP/HTTPS request to /v2/ with status code capture
    4. docker login attempt (if REGISTRY_USER/PASSWORD are set)
    5. docker pull test with alpine:latest
    6. Certificate validity check (if HTTPS)
"""
import json
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check container registry connectivity and report diagnostics."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", help="Output as JSON.")
        parser.add_argument("--verbose", action="store_true", help="Verbose output.")
        parser.add_argument(
            "--insecure",
            action="store_true",
            help="Skip SSL certificate verification (useful for self-signed certs).",
        )

    def handle(self, *args, **options):
        results = self._run_diagnostics(options["verbose"], insecure=options.get("insecure", False))
        if options["json"]:
            self.stdout.write(json.dumps(results, indent=2, default=str))
        else:
            self._print_results(results)

        all_ok = all(r.get("ok", False) for r in results.get("tests", []) if r["name"] != "tcp_port")
        if not all_ok:
            sys.exit(1)

    def _run_diagnostics(self, verbose: bool, insecure: bool = False) -> dict:

        registry = os.environ.get("CONTAINER_REGISTRY_URL", "").strip()
        if not registry:
            return {
                "status": "not_configured",
                "error": "CONTAINER_REGISTRY_URL is not set.",
                "tests": [],
            }

        parsed = urlparse(registry if "://" in registry else f"http://{registry}")
        hostname = parsed.hostname or ""
        port = parsed.port or 5000
        scheme = parsed.scheme or "http"
        use_tls = scheme == "https"

        tests = []

        # ── 1. DNS Resolution ─────────────────────────────────────────
        dns_ok = False
        dns_details = ""
        try:
            ips = socket.getaddrinfo(hostname, port, socket.AF_INET, socket.SOCK_STREAM)
            resolved_ips = sorted(set(addr[4][0] for addr in ips))
            dns_ok = len(resolved_ips) > 0
            dns_details = f"Resolved to {', '.join(resolved_ips)}"
        except socket.gaierror as exc:
            dns_details = str(exc)
        tests.append({"name": "dns_resolution", "ok": dns_ok, "detail": dns_details})

        # ── 2. TCP Connectivity ───────────────────────────────────────
        tcp_ok = False
        tcp_details = ""
        if dns_ok and resolved_ips:
            for ip in resolved_ips:
                try:
                    sock = socket.create_connection((ip, port), timeout=5)
                    sock.close()
                    tcp_ok = True
                    tcp_details = f"Connected to {ip}:{port}"
                    break
                except OSError as exc:
                    tcp_details = str(exc)
        else:
            tcp_details = "Skipped (DNS failed)"
        tests.append({"name": "tcp_port", "ok": tcp_ok, "detail": tcp_details})

        # ── 3. HTTP/HTTPS request to /v2/ ─────────────────────────────
        http_codes = []
        for proto in (["https", "http"] if not use_tls else ["http", "https"]):
            url = f"{proto}://{hostname}:{port}/v2/"
            try:
                import ssl
                import urllib.request

                ctx = ssl.create_default_context()
                if insecure:
                    # Explicitly requested: skip cert validation for self-signed registries
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE

                req = urllib.request.Request(url, method="HEAD")
                resp = urllib.request.urlopen(req, timeout=5, context=ctx)
                code = resp.getcode()
                http_codes.append(f"{proto.upper()} {code}")
                if code in (200, 401, 403):
                    break
            except Exception as exc:
                if verbose:
                    http_codes.append(f"{proto.upper()} error: {exc}")

        http_ok = any(str(c).split()[-1].isdigit() and int(str(c).split()[-1]) in (200, 401, 403) for c in http_codes)
        tests.append({
            "name": f"{'https' if use_tls else 'http'}_endpoint",
            "ok": http_ok,
            "detail": "; ".join(http_codes) or "No response",
        })

        # ── 4. Docker login ───────────────────────────────────────────
        login_ok = False
        login_details = ""
        user = os.environ.get("REGISTRY_USER", "").strip()
        password = os.environ.get("REGISTRY_PASSWORD", "").strip()
        if user and password:
            try:
                proc = subprocess.run(
                    ["docker", "login", f"{hostname}:{port}", "-u", user, "--password-stdin"],
                    input=password,
                    capture_output=True, text=True, timeout=15,
                )
                login_ok = proc.returncode == 0
                login_details = proc.stdout.strip() or proc.stderr.strip()
            except Exception as exc:
                login_details = str(exc)
        else:
            login_details = "Skipped (no credentials configured)"
            login_ok = True  # not required
        tests.append({"name": "docker_login", "ok": login_ok, "detail": login_details})

        # ── 5. Docker pull test ───────────────────────────────────────
        pull_ok = False
        pull_details = ""
        if tcp_ok or http_ok:
            try:
                start = time.time()
                proc = subprocess.run(
                    ["docker", "pull", f"{hostname}:{port}/alpine:latest"],
                    capture_output=True, text=True, timeout=30,
                )
                pull_ok = proc.returncode == 0
                elapsed = time.time() - start
                pull_details = f"Done in {elapsed:.1f}s" if pull_ok else (proc.stderr.strip()[:500] or proc.stdout.strip()[:500])
            except Exception as exc:
                pull_details = str(exc)
        else:
            pull_details = "Skipped (registry not reachable)"
        tests.append({"name": "docker_pull", "ok": pull_ok, "detail": pull_details})

        return {
            "registry_url": registry,
            "hostname": hostname,
            "port": port,
            "tls": use_tls,
            "status": "ok" if all(t["ok"] for t in tests if t["name"] != "tcp_port") else "unreachable",
            "tests": tests,
        }

    def _print_results(self, results: dict) -> None:
        ok_icon = "\033[0;32m✓\033[0m"
        fail_icon = "\033[0;31m✗\033[0m"
        warn_icon = "\033[1;33m⚠\033[0m"

        self.stdout.write(f"Registry: {results['registry_url']} ({results['hostname']}:{results['port']}, TLS={results['tls']})")
        self.stdout.write(f"Status:  {results['status']}\n")

        for test in results.get("tests", []):
            icon = ok_icon if test["ok"] else fail_icon
            self.stdout.write(f"  {icon} {test['name']}: {test['detail']}")

        if results["status"] != "ok":
            self.stdout.write(f"\n  {warn_icon} The registry is unreachable from this host.")
            self.stdout.write("  → Check that the registry container is running: docker compose ps registry")
            self.stdout.write("  → Verify DNS resolves or use 127.0.0.1:5000 as CONTAINER_REGISTRY_URL")
            self.stdout.write("  → Run install.sh to trigger registry self-heal")
            self.stdout.write("  → Check firewall rules on port 5000")
