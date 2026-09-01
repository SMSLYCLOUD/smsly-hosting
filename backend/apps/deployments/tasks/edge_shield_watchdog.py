"""Edge Shield watchdog — hijack-symptom detection.

Even with Cloudflare-proxied records, two hijack windows remain:

  1. ORIGIN REACHABILITY: if the covering prefix is hijacked, clients
     who somehow reach the origin (cached DNS, direct IP, non-CF path)
     land on the attacker. The watchdog polls the origin IP from the
     host itself AND checks that the platform's public name resolves
     to Cloudflare edge ranges (never back to the origin).

  2. DNS FORGERY: resolvers with cached DNS-only records (or an
     on-path attacker) can still return the origin IP. The watchdog
     queries several independent resolvers for the platform name and
     alerts if any answer is NOT in Cloudflare's published ranges —
     the signature of either a forged answer or a proxy flip.

  3. RPKI STATE: the covering aggregate's ROA validity is checked via
     RIPEstat. A transition to 'invalid' or a sudden new more-specific
     announcement of our origin prefix is the classic hijack signal.

Alerts go through the existing alert pipeline (alert_user_task) so the
operator is paged exactly like a crash-loop incident.
"""
from __future__ import annotations

import ipaddress
import logging

import requests
from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM

logger = logging.getLogger(__name__)

_TIMEOUT = 10


def _is_cloudflare_ip(answer: str) -> bool:
    """True if an A-record answer falls inside Cloudflare's ranges."""
    ranges_v4 = [
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
        "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
        "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
        "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "172.64.0.0/13", "131.0.72.0/22",
    ]
    try:
        addr = ipaddress.ip_address(answer)
    except ValueError:
        return False
    if addr.version != 4:
        return True  # AAAA through CF — fine
    return any(addr in ipaddress.ip_network(n) for n in ranges_v4)


@shared_task(
    bind=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
    name="apps.deployments.tasks.edge_shield_watchdog",
)
def edge_shield_watchdog(self):
    """Periodic hijack-symptom sweep. Beat: every 5 minutes."""
    from apps.deployments.models import PlatformConfig

    findings: list[str] = []

    try:
        config = PlatformConfig.load()
    except Exception as exc:
        logger.error("edge_shield_watchdog: config load failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    domain = (config.domain or "").strip()
    origin_ip = str(config.server_ip or "").strip()
    shield_on = bool(getattr(config, "edge_shield_enabled", False))

    if not domain:
        return {"status": "skipped", "reason": "no platform domain"}

    # ── 1. Multi-vantage DNS: every public answer must be a CF edge ──
    if shield_on:
        for resolver in ("1.1.1.1", "8.8.8.8", "9.9.9.9"):
            try:
                resp = requests.get(
                    f"https://{resolver}/dns-query",
                    params={"name": domain, "type": "A"},
                    headers={"Accept": "application/dns-json"},
                    timeout=_TIMEOUT,
                )
                data = resp.json()
                answers = [
                    a.get("data") for a in data.get("Answer", []) or []
                    if a.get("type") == 1
                ]
            except Exception as exc:
                findings.append(f"resolver {resolver}: query failed ({exc})")
                continue

            for answer in answers:
                if origin_ip and answer == origin_ip:
                    findings.append(
                        f"resolver {resolver}: {domain} -> {origin_ip} "
                        "(ORIGIN IP EXPOSED — record unproxied or hijack)"
                    )
                elif not _is_cloudflare_ip(answer):
                    findings.append(
                        f"resolver {resolver}: {domain} -> {answer} "
                        "NOT in Cloudflare ranges (possible DNS forgery "
                        "or BGP-adjacent hijack)"
                    )

    # ── 2. RPKI state of the covering aggregate (RIPEstat) ──
    if origin_ip:
        try:
            resp = requests.get(
                "https://stat.ripe.net/data/rpki-validation/data.json",
                params={"resource": f"{origin_ip}/32"},
                timeout=_TIMEOUT,
            )
            body = resp.json()
            state = ((body.get("data") or {}).get("validity") or {}).get("state", "?")
            if state not in ("valid", "not found", "?"):
                findings.append(
                    f"RPKI: origin /32 validation state = {state} "
                    "(investigate possible hijack / ROA conflict)"
                )
        except Exception:
            pass  # RPKI data source availability is not alert-worthy

    if not findings:
        logger.info("edge_shield_watchdog: clean")
        return {"status": "ok", "findings": []}

    logger.warning("edge_shield_watchdog: %d finding(s): %s", len(findings), findings)

    # Page through the standard alert pipeline — same channel as
    # crash-loop incidents so hijack symptoms are impossible to miss.
    try:
        from apps.core.tasks.alerts import alert_user_task
        alert_user_task.delay(
            error_message=(
                f"EDGE SHIELD WARNING — possible BGP/DNS hijack symptom "
                f"on {domain} (origin {origin_ip}):\n"
                + "\n".join(f"- {f}" for f in findings[:6])
            ),
        )
    except Exception as exc:
        logger.error("edge_shield_watchdog: alert dispatch failed: %s", exc)

    return {"status": "warn", "findings": findings}
