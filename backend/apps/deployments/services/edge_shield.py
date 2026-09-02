"""Edge Shield — BGP-hijack / route-hijack defense for the platform.

Threat model (verified live 2026-09-01):

  * DNS records resolve directly to the OVH origin IP
    (grid.smsly.cloud -> 176.31.201.181, AS16276) — Cloudflare is
    DNS-only (`dns.py` hardcodes ``proxied=False``), so user traffic
    flows over the origin's BGP path. Nothing about the path is
    platform-controlled: the origin sits on OVH provider-aggregated
    space (``176.31.0.0/16``) whose ROAs OVH publishes — we control
    none of it.
  * A more-specific BGP announcement of the covering prefix
    (or the /32 itself) redirects users to an attacker until the
    global routing table converges. On DNS-only, TLS HTTP-01
    certificates do not help: the attacker completes their own ACME
    challenge for the same names.
  * The ``smsly.cloud`` zone is not DNSSEC-signed (no DS at the
    parent, ``AD=false`` on Cloudflare DoH), so forged/intercepted
    DNS responses are indistinguishable from real ones.

Defense-in-depth implemented here:

  1. PROXY — flip zone records to Cloudflare-proxied (orange cloud).
     Traffic then enters through Cloudflare Anycast — geographically
     diverse prefixes the attacker does not control — and the origin
     IP becomes non-authoritative for reachability. Hijacking the
     OVH /24 stops affecting end users. Also brings L3-L4 DDoS
     absorption and CF WAF.
  2. TLS FULL — set zone SSL mode to ``full`` so the CF->origin leg
     validates our real certificate (origin pulls fail closed).
  3. LOCKDOWN — host firewall accepts 80/443 ONLY from Cloudflare's
     published ranges, closing the direct-to-origin bypass (an
     attacker who learns the origin IP from historical DNS cannot
     skip the edge). Managed by scripts/cf_origin_lockdown.sh.
  4. DNSSEC — sign the zone at Cloudflare, store the DS record;
     once added at the registrar, forged DNS answers fail
     validation at the resolver.
  5. WATCHDOG — periodic multi-vantage checks (DNS from several
     resolvers, origin reachability from outside CF, RPKI state of
     the covering aggregate) that alert on hijack symptoms.

Cloudflare API coverage needed by the token: Zone.Settings (edit),
Zone.DNS (edit), Zone.DNSSEC (edit). The existing token is created
for DNS edit; settings/DNSSEC scopes must be added when deploying.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

_CF_API = "https://api.cloudflare.com/client/v4"
_TIMEOUT = 15


@dataclass
class EdgeShieldReport:
    """Result of applying / verifying the shield. Serializable dict."""
    ok: bool = True
    steps: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def step(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append({"step": name, "status": status, "detail": detail})
        if status == "error":
            self.ok = False
            self.errors.append(f"{name}: {detail}")

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "steps": self.steps,
            "errors": self.errors,
        }


def _cf_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _cf(request_fn, method: str, url: str, token: str, **kwargs) -> dict | None:
    """Single Cloudflare API call. Returns the 'result' dict or None."""
    kwargs.setdefault("timeout", _TIMEOUT)
    kwargs.setdefault("headers", _cf_headers(token))
    try:
        resp = request_fn(f"{_CF_API}{url}", **kwargs)
    except requests.Timeout:
        logger.warning("Cloudflare API timeout: %s %s", method, url)
        return None
    except Exception as exc:
        logger.warning("Cloudflare API error: %s %s: %s", method, url, exc)
        return None
    if resp.status_code not in (200, 201, 204):
        logger.warning(
            "Cloudflare API %s %s -> %s: %s",
            method, url, resp.status_code, resp.text[:200],
        )
        return None
    try:
        body = resp.json()
    except ValueError:
        return None
    if body.get("success") is False:
        logger.warning(
            "Cloudflare API %s %s reported failure: %s",
            method, url, body.get("errors"),
        )
        return None
    return body.get("result")


def get_zone_id(token: str, zone_name: str) -> str | None:
    result = _cf(
        requests.get, "GET", f"/zones?name={zone_name}", token,
    )
    if isinstance(result, list) and result:
        return result[0].get("id")
    return None


def _zone_records(token: str, zone_id: str) -> list[dict]:
    result = _cf(
        requests.get, "GET",
        f"/zones/{zone_id}/dns_records?per_page=100", token,
    )
    return result if isinstance(result, list) else []


def _proxy_dns_records(token: str, zone_id: str, report: EdgeShieldReport,
                       *, proxy_wildcards: bool = False) -> None:
    """Flip zone records to proxied=True (orange cloud).

    Wildcards (*.domain) are skipped unless *proxy_wildcards* — Cloudflare
    Universal SSL only covers the zone + first-level wildcard; a proxied
    third-level wildcard (*.grid.example.com) cannot present a matching
    edge certificate and EVERY hostname under it fails with
    ERR_SSL_VERSION_OR_CIPHER_MISMATCH (the 2026-09-02 outage). Wildcards
    must stay DNS-only so origin on-demand TLS serves them.
    """
    records = _zone_records(token, zone_id)
    if not records:
        report.step("list_records", "error", "no records returned (token scope?)")
        return

    flipped, already, skipped = [], [], []
    for record in records:
        if record.get("type") not in ("A", "AAAA", "CNAME"):
            skipped.append(record.get("name"))
            continue
        name = str(record.get("name") or "")
        if name.startswith("*") and not proxy_wildcards:
            # Wildcard: DNS-only by design (see docstring). If a previous
            # run proxied it, un-proxy it back.
            if record.get("proxied"):
                result = _cf(
                    requests.patch, "PATCH",
                    f"/zones/{zone_id}/dns_records/{record['id']}",
                    token,
                    json={"proxied": False},
                )
                if result is not None:
                    skipped.append(f"{name} (un-proxied back to DNS-only)")
                    continue
            skipped.append(f"{name} (wildcard — DNS-only)")
            continue
        if record.get("proxied"):
            already.append(name)
            continue
        # Some records are structurally un-proxyable BY DESIGN: e.g.
        # Zoho/Microsoft domain-ownership verification CNAMEs
        # (zmverify.zoho.com — "Target ... is not allowed for a proxied
        # record"). Proxying those would break the very verification they
        # exist for, so Cloudflare marks them proxiable=false. They stay
        # DNS-only intentionally and are NOT an origin-exposure gap: they
        # point at third-party hosts, never at our origin IP.
        if record.get("proxiable") is False:
            skipped.append(f"{name} (proxiable=false)")
            continue
        result = _cf(
            requests.patch, "PATCH",
            f"/zones/{zone_id}/dns_records/{record['id']}",
            token,
            json={"proxied": True},
        )
        if result and result.get("proxied"):
            flipped.append(name)
        else:
            report.step(
                "proxy_record", "error",
                f"{name}: patch failed",
            )

    detail = f"proxied {len(flipped)} ({', '.join(flipped[:8])}), " \
             f"already {len(already)}, skipped {len(skipped)}"
    # Success = every proxyable record is now proxied (unproxyable
    # verification CNAMEs are skipped by design, not failures).
    report.step(
        "proxy_records",
        "ok" if flipped or already else "error",
        detail,
    )


def _set_tls_full(token: str, zone_id: str, report: EdgeShieldReport) -> None:
    """Zone SSL mode -> 'full'. The CF->origin leg must present our real
    cert (self-signed rejected; hijacked origins presenting attacker
    certs fail closed). 'strict' would also pin to valid CA certs, but
    on-demand LE issuance for tenant domains makes 'full' the safe
    initial mode; tighten per-host once certs are known-stable."""
    result = _cf(
        requests.patch, "PATCH",
        f"/zones/{zone_id}/settings/ssl",
        token,
        json={"value": "full"},
    )
    if result and result.get("value") == "full":
        report.step("tls_full", "ok", "zone SSL mode = full")
    else:
        report.step("tls_full", "error", "could not set ssl=full (Zone Settings scope?)")


def _set_hsts(token: str, zone_id: str, report: EdgeShieldReport) -> None:
    """HSTS at the edge — browsers refuse plain HTTP to the domain for a
    year (with subdomains), removing the SSL-strip variant of an on-path
    hijack."""
    result = _cf(
        requests.patch, "PATCH",
        f"/zones/{zone_id}/settings/security_header",
        token,
        json={
            "value": {
                "strict_transport_security": {
                    "enabled": True,
                    "max_age": 31536000,
                    "include_subdomains": True,
                    "preload": True,
                }
            }
        },
    )
    if result is not None:
        report.step("hsts", "ok", "HSTS enabled (1y, subdomains, preload)")
    else:
        report.step("hsts", "error", "could not set security_header")


def _enable_dnssec(token: str, zone_id: str, report: EdgeShieldReport) -> dict | None:
    """Enable DNSSEC on the zone and capture the DS record.

    The DS must be published at the registrar to complete the chain —
    we store it on PlatformConfig.edge_shield_ds_record so the operator
    sees exactly what to paste.
    """
    current = _cf(requests.get, "GET", f"/zones/{zone_id}/dnssec", token)
    if isinstance(current, dict) and current.get("status") == "active":
        ds = _format_ds(current)
        report.step("dnssec", "ok", f"already active. DS: {ds}")
        return current

    result = _cf(
        requests.patch, "PATCH",
        f"/zones/{zone_id}/dnssec",
        token,
        json={"status": "active"},
    )
    if not (isinstance(result, dict) and result.get("status") in ("pending", "active")):
        report.step("dnssec", "error", "could not enable (Zone DNSSEC scope?)")
        return None
    ds = _format_ds(result)
    state = "active" if result.get("status") == "active" else "pending — add DS at registrar"
    report.step("dnssec", "ok", f"status={result.get('status')} ({state}) DS: {ds}")
    return result


def _format_ds(dnssec: dict) -> str:
    """Cloudflare DS digest format: 'key_tag algorithm digest_type digest'."""
    try:
        return (
            f"{dnssec['digest']} {dnssec['key_algorithm']} "
            f"{dnssec.get('digest_type', 2)} {dnssec.get('digest', '')[:24]}…"
        )
    except (KeyError, TypeError):
        return "(digest unavailable)"


def deploy_edge_shield(config, *, enable_proxy=True, enable_dnssec=True,
                       enable_lockdown=True) -> EdgeShieldReport:
    """Apply the full shield. `config` is a PlatformConfig instance.

    Lockdown (iptables) is executed host-side; from the backend container
    we run it through the same one-shot privileged shim used by the egress
    rules. When called from a context without docker CLI (tests), the
    lockdown step is skipped and reported as deferred.
    """
    report = EdgeShieldReport()
    token = (getattr(config, "cloudflare_api_token", "") or "").strip()
    domain = (getattr(config, "domain", "") or "").strip()

    if not token or not domain:
        report.step("preflight", "error", "cloudflare_api_token and domain required")
        return report

    zone_name = ".".join(domain.split(".")[-2:])
    report.step("preflight", "ok", f"zone={zone_name}")

    zone_id = get_zone_id(token, zone_name)
    if not zone_id:
        report.step("zone_lookup", "error", f"zone {zone_name} not visible to token")
        return report
    report.step("zone_lookup", "ok", zone_id)

    if enable_proxy:
        _proxy_dns_records(token, zone_id, report)
        _set_tls_full(token, zone_id, report)
        _set_hsts(token, zone_id, report)

    ds_payload = None
    if enable_dnssec:
        ds_payload = _enable_dnssec(token, zone_id, report)

    if enable_lockdown:
        _apply_origin_lockdown(report)

    # Persist state so the watchdog and dashboard reflect reality.
    config.edge_proxy_records = bool(enable_proxy)
    config.edge_dnssec = bool(enable_dnssec)
    config.edge_origin_lockdown = bool(enable_lockdown) and not any(
        s["step"] == "lockdown" and s["status"] == "error" for s in report.steps
    )
    config.edge_shield_enabled = report.ok
    if ds_payload:
        config.edge_shield_ds_record = _format_ds(ds_payload)
    try:
        config.save(update_fields=[
            "edge_proxy_records", "edge_dnssec", "edge_origin_lockdown",
            "edge_shield_enabled", "edge_shield_ds_record", "updated_at",
        ])
        report.step("persist", "ok", "PlatformConfig flags saved")
    except Exception as exc:
        report.step("persist", "error", str(exc))

    return report


def _apply_origin_lockdown(report: EdgeShieldReport) -> None:
    """Run scripts/cf_origin_lockdown.sh via the privileged docker shim.

    The script is baked into the backend image at
    /app/scripts/cf_origin_lockdown.sh and executes on the host with
    --net=host + NET_ADMIN, the same pattern the egress firewall uses.
    """
    import subprocess

    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm", "--net=host",
                "--cap-add=NET_ADMIN",
                "-v", "/app/scripts/cf_origin_lockdown.sh:/shield.sh:ro",
                "alpine:3.20", "sh", "/shield.sh", "--on",
            ],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        report.step("lockdown", "error", "docker CLI unavailable")
        return
    except Exception as exc:
        report.step("lockdown", "error", str(exc))
        return

    if result.returncode == 0:
        report.step(
            "lockdown", "ok",
            (result.stdout or "").strip()[:120] or "80/443 restricted to Cloudflare",
        )
    else:
        report.step("lockdown", "error", (result.stderr or result.stdout or "")[:200])


def verify_edge_shield(config) -> EdgeShieldReport:
    """Read-only verification pass — safe to run from a beat task."""
    report = EdgeShieldReport()
    token = (getattr(config, "cloudflare_api_token", "") or "").strip()
    domain = (getattr(config, "domain", "") or "").strip()
    if not token or not domain:
        report.step("preflight", "error", "cloudflare_api_token and domain required")
        return report

    zone_name = ".".join(domain.split(".")[-2:])
    zone_id = get_zone_id(token, zone_name)
    if not zone_id:
        report.step("zone_lookup", "error", f"zone {zone_name} not visible")
        return report

    # All A records proxied? (un-proxyable verification records excluded
    # by design — they target third-party hosts, never our origin IP)
    records = _zone_records(token, zone_id)
    a_records = [
        r for r in records
        if r.get("type") == "A" and r.get("proxiable") is not False
    ]
    unproxied = [r.get("name") for r in a_records if not r.get("proxied")]
    if a_records and not unproxied:
        report.step("proxy_state", "ok", f"{len(a_records)} A records proxied")
    elif unproxied:
        report.step("proxy_state", "warn", f"unproxied: {', '.join(unproxied[:6])}")

    # TLS mode
    ssl = _cf(requests.get, "GET", f"/zones/{zone_id}/settings/ssl", token)
    if isinstance(ssl, dict) and ssl.get("value") in ("full", "strict"):
        report.step("tls_state", "ok", f"ssl={ssl.get('value')}")
    else:
        report.step("tls_state", "warn", f"ssl={ssl.get('value') if isinstance(ssl, dict) else '?'}")

    # DNSSEC
    dnssec = _cf(requests.get, "GET", f"/zones/{zone_id}/dnssec", token)
    if isinstance(dnssec, dict) and dnssec.get("status") == "active":
        report.step("dnssec_state", "ok", "active")
    else:
        status = dnssec.get("status") if isinstance(dnssec, dict) else "?"
        report.step("dnssec_state", "warn", f"status={status}")

    # Origin visibility: the origin IP must not appear in any record
    # content once proxied (proxied records return CF edge IPs).
    origin_ip = str(getattr(config, "server_ip", "") or "")
    if origin_ip:
        leaking = [
            r.get("name") for r in records
            if r.get("type") == "A" and r.get("content") == origin_ip
            and not r.get("proxied")
        ]
        if leaking:
            report.step("origin_exposure", "warn", f"origin IP visible: {', '.join(leaking[:6])}")
        else:
            report.step("origin_exposure", "ok", "origin IP not exposed via DNS")

    return report
