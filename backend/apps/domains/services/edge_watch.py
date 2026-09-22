# pylint: disable=invalid-name
"""
Edge watchdog: verify the platform is reachable the way the outside world
sees it, and self-heal DNS drift.

On-host probes lie when the failure is at the edge (firewall, DNS,
proxy state): loopback succeeds while the world times out. This task,
every 10 minutes:

1. Resolves the platform domain through public DNS (DoH, not the host
   resolver, which may carry overrides).
2. Compares against desire: Cloudflare edge IPs when ``edge_proxy_records``
   is on, else the server IP.
3. Performs a real TLS handshake + ``GET /`` against the resolved IP with
   SNI (the exact client behavior that was failing).
4. On DNS drift toward grey-while-desired-orange, flips the record back
   to proxied via the Cloudflare API (one-way only — never downgrades),
   using the stored API token, and audit-logs the change.

Never raises — a broken watchdog must not take down the beat worker.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import ssl
import time
import urllib.request

logger = logging.getLogger(__name__)

# Bundled fallback when https://www.cloudflare.com/ips-v4 is unreachable.
CF_IPV4_FALLBACK = (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
    "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
    "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
    "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
)

DOH_URL = "https://cloudflare-dns.com/dns-query?name={name}&type={rtype}"
WATCHDOG_TIMEOUT = 15
CACHE_KEY_LAST = "edge:watch:last"


def _http_json(url, timeout=10):
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def public_dns_ips(domain, rtype="A"):
    """A/AAAA answers from public DNS (never the host resolver)."""
    try:
        data = _http_json(DOH_URL.format(name=domain, rtype=rtype))
        return [a.get("data", "") for a in data.get("Answer", [])
                if a.get("type") in (1, 28) and a.get("data")]
    except Exception as exc:
        logger.debug("Edge watch DoH failed for %s: %s", domain, exc)
        return []


def cf_ipv4_ranges():
    """Live Cloudflare edge ranges, bundled fallback."""
    try:
        req = urllib.request.Request("https://www.cloudflare.com/ips-v4")
        with urllib.request.urlopen(req, timeout=10) as resp:
            nets = [l.strip() for l in resp.read().decode().splitlines() if l.strip()]
            if nets:
                return nets
    except Exception as exc:
        logger.debug("Edge watch CF ranges fetch failed: %s", exc)
    return list(CF_IPV4_FALLBACK)


def is_cf_edge_ip(ip, ranges=None):
    """True when ip belongs to Cloudflare edge space."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in ranges or cf_ipv4_ranges():
        try:
            if addr in ipaddress.ip_network(net):
                return True
        except ValueError:
            continue
    return False


def probe_public_url(domain, timeout=WATCHDOG_TIMEOUT):
    """TLS handshake + GET / against publicly-resolved IP with SNI.

    Returns ``(ok, detail)``. Mirrors exactly what an outside browser
    does, bypassing any host resolver overrides.
    """
    ips = public_dns_ips(domain, "A")
    if not ips:
        return False, "no public A records"
    last_error = ""
    for ip in ips[:3]:
        try:
            raw = socket.create_connection((ip, 443), timeout=timeout)
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(raw, server_hostname=domain)
            try:
                tls.sendall(
                    f"GET / HTTP/1.1\r\nHost: {domain}\r\n"
                    "Connection: close\r\nUser-Agent: smsly-edge-watch\r\n\r\n"
                    .encode())
                head = b""
                tls.settimeout(timeout)
                while b"\r\n\r\n" not in head and len(head) < 65536:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    head += chunk
                line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
                parts = line.split(" ", 2)
                code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                if 200 <= code < 500:
                    return True, f"{ip} HTTP {code}"
                last_error = f"{ip} HTTP {code}"
            finally:
                try:
                    tls.close()
                except Exception:
                    pass
        except Exception as exc:
            last_error = f"{ip}: {exc}"
    return False, last_error or "unreachable"


def check_edge():
    """Run the full edge verification; returns a report dict, never raises."""
    report = {"ok": True, "checks": {}, "healed": [], "errors": []}
    try:
        from apps.deployments.models import PlatformConfig
        cfg = PlatformConfig.load()
        domain = (getattr(cfg, "domain", "") or "").strip().rstrip(".")
        want_proxy = bool(getattr(cfg, "edge_proxy_records", False))
        server_ip = (getattr(cfg, "server_ip", "") or "").strip()
    except Exception as exc:
        return {"ok": False, "checks": {}, "healed": [],
                "errors": [f"config load failed: {exc}"]}
    if not domain:
        report["errors"].append("no platform domain configured")
        report["ok"] = False
        return report

    live_a = public_dns_ips(domain, "A")
    report["checks"]["public_a"] = live_a
    if not live_a:
        report["ok"] = False
        report["errors"].append("apex has no public A records")
        return report
    all_edge = all(is_cf_edge_ip(ip) for ip in live_a)
    report["checks"]["proxied"] = all_edge
    if want_proxy and not all_edge:
        report["ok"] = False
        report["errors"].append(
            f"DNS drift: edge_proxy_records is on but {domain} resolves "
            f"directly ({', '.join(live_a)}) — proxy missing")
        healed = _heal_proxy_state(cfg, domain)
        if healed:
            report["healed"].append(healed)
    elif not want_proxy and server_ip and not all(
            ip == server_ip for ip in live_a):
        report["checks"]["origin_mismatch"] = True
        report["errors"].append(
            f"DNS points away from this server ({', '.join(live_a)})")

    ok, detail = probe_public_url(domain)
    report["checks"]["probe"] = detail
    if not ok:
        report["ok"] = False
        report["errors"].append(f"public probe failed: {detail}")
    return report


def _heal_proxy_state(cfg, domain):
    """Re-enable proxy on a drifted apex record. Returns detail or ''.

    One-way only (grey -> orange); uses the stored Cloudflare token and
    audit-logs. Any failure returns '' — the alert below still fires.
    """
    try:
        from apps.domains.services import dns as dns_svc
        token = (getattr(cfg, "cloudflare_api_token", "") or "").strip()
        server_ip = (getattr(cfg, "server_ip", "") or "").strip()
        if not token or not server_ip:
            return ""
        zone = dns_svc._guess_zone_name(domain)
        zone_id = dns_svc._get_zone_id(token, zone)
        if not zone_id:
            return ""
        changed = False
        for record in dns_svc._get_records(token, zone_id, domain, "A") or []:
            if record.get("content") == server_ip and not record.get("proxied", False):
                ok, _ = dns_svc._update_record(
                    token, zone_id, record.get("id", ""), domain,
                    server_ip, proxied=True)
                changed = changed or ok
        if not changed:
            return ""
        detail = f"re-proxied {domain} (drift heal)"
        logger.warning("Edge watch: %s", detail)
        try:
            from apps.deployments.models.audit import AuditLog
            AuditLog(actor="edge-watchdog", action="EDGE_DNS_HEAL",
                     target=f"Domain: {domain}",
                     metadata={"domain": domain}).save()
        except Exception:
            pass
        return detail
    except Exception as exc:
        logger.debug("Edge watch heal failed for %s: %s", domain, exc)
        return ""


def watch_edge_task_body():
    """Beat-task body (seam for tests): run check, cache + log outcome."""
    from django.core.cache import cache
    report = check_edge()
    try:
        cache.set(CACHE_KEY_LAST, {
            "ok": report["ok"],
            "errors": report["errors"][:5],
            "healed": report["healed"],
            "at": time.time(),
        }, timeout=3600)
    except Exception:
        pass
    if not report["ok"]:
        logger.error("Edge watch failing: %s", "; ".join(report["errors"][:5]))
    else:
        logger.info("Edge watch ok")
    return report
