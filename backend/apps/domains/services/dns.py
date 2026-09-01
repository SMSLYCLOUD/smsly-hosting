"""DNS helpers for automatic Cloudflare record management."""
from __future__ import annotations

import logging
from collections.abc import Iterable

import requests

logger = logging.getLogger(__name__)


def _guess_zone_name(domain: str) -> str:
    """Naive public suffix guess: last two labels."""
    parts = domain.split(".")
    if len(parts) < 2:
        return domain
    return ".".join(parts[-2:])


def _get_zone_id(token: str, zone_name: str) -> str | None:
    url = f"https://api.cloudflare.com/client/v4/zones?name={zone_name}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning("Cloudflare zones lookup failed (%s): %s", resp.status_code, resp.text)
            return None
        data = resp.json()
        if data.get("success") and data.get("result"):
            return data["result"][0]["id"]
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Cloudflare zones lookup error: %s", exc)
    return None


def _get_records(token: str, zone_id: str, name: str, record_type: str) -> list[dict]:
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    params = {"type": record_type, "name": name}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if data.get("success") and isinstance(data.get("result"), list):
            return data["result"]
        return []
    except Exception:
        return []


def _record_exists(token: str, zone_id: str, name: str, record_type: str) -> bool:
    return bool(_get_records(token, zone_id, name, record_type))


def _create_record(token: str, zone_id: str, name: str, content: str, proxied: bool = False) -> tuple[bool, str]:
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"type": "A", "name": name, "content": content, "proxied": proxied, "ttl": 120}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            body = resp.json()
            if body.get("success"):
                return True, "created"
            return False, str(body)
        return False, resp.text
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return False, str(exc)


def _update_record(token: str, zone_id: str, record_id: str, name: str, content: str, proxied: bool = False) -> tuple[bool, str]:
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"type": "A", "name": name, "content": content, "proxied": proxied, "ttl": 120}
    try:
        resp = requests.put(url, headers=headers, json=payload, timeout=10)
        if resp.status_code in (200, 201):
            body = resp.json()
            if body.get("success"):
                return True, "updated"
            return False, str(body)
        return False, resp.text
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return False, str(exc)


def delete_dns_record(domain: str, token: str) -> bool:
    """Delete an A record for the given domain from Cloudflare.

    Returns True if the record was deleted or didn't exist.
    """
    if not domain or not token:
        return False
    zone_name = _guess_zone_name(domain)
    zone_id = _get_zone_id(token, zone_name)
    if not zone_id:
        return False
    records = _get_records(token, zone_id, domain, "A")
    if not records:
        return True
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    ok = True
    for record in records:
        record_id = record.get("id")
        if not record_id:
            continue
        url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}"
        try:
            resp = requests.delete(url, headers=headers, timeout=10)
            if resp.status_code not in (200, 404):
                logger.warning("Failed to delete DNS record %s (%s): %s", domain, record_id, resp.text)
                ok = False
        except Exception as exc:
            logger.warning("Failed to delete DNS record %s (%s): %s", domain, record_id, exc)
            ok = False
    return ok


def _desired_proxied_state() -> bool:
    """Resolve whether records should be Cloudflare-proxied.

    Edge Shield: when `edge_proxy_records` is enabled, ALL records this
    module manages must stay proxied (orange cloud) so traffic flows
    through Cloudflare Anycast — that is the core BGP-hijack defense.
    When the shield is off, preserve the legacy DNS-only behavior.

    Read lazily per-call (not import time) so tests can toggle flags.
    """
    try:
        from apps.deployments.models import PlatformConfig
        return bool(PlatformConfig.load().edge_proxy_records)
    except Exception:
        return False


def ensure_dns_records(domains: Iterable[str], server_ip: str, token: str) -> dict:
    """
    Ensure A records exist for each domain pointing to server_ip in Cloudflare.

    Returns summary dict with successes and errors.
    """
    result: dict = {"ok": True, "created": [], "updated": [], "skipped": [], "errors": []}
    token = (token or "").strip()
    server_ip = (server_ip or "").strip()
    domains = [d.strip().lower() for d in domains if d and d.strip()]

    if not token or not server_ip or not domains:
        result["ok"] = False
        result["errors"].append("missing token/server_ip/domains")
        return result

    desired_proxied = _desired_proxied_state()
    for domain in domains:
        zone_name = _guess_zone_name(domain)
        zone_id = _get_zone_id(token, zone_name)
        if not zone_id:
            result["ok"] = False
            result["errors"].append(f"{domain}: zone not found")
            continue

        records = _get_records(token, zone_id, domain, "A")
        if records:
            changed = False
            for record in records:
                if record.get("content") == server_ip and bool(record.get("proxied", False)) == desired_proxied:
                    continue
                updated, msg = _update_record(
                    token,
                    zone_id,
                    record.get("id", ""),
                    domain,
                    server_ip,
                    proxied=desired_proxied,
                )
                if updated:
                    changed = True
                else:
                    result["ok"] = False
                    result["errors"].append(f"{domain}: {msg}")
            if changed:
                result["updated"].append(domain)
            elif result["ok"]:
                result["skipped"].append(domain)
            continue

        cname_records = _get_records(token, zone_id, domain, "CNAME")
        if cname_records:
            updated, msg = _update_record(
                token,
                zone_id,
                cname_records[0].get("id", ""),
                domain,
                server_ip,
                proxied=desired_proxied,
            )
            if updated:
                result["updated"].append(domain)
            else:
                result["ok"] = False
                result["errors"].append(f"{domain}: {msg}")
            continue

        created, msg = _create_record(token, zone_id, domain, server_ip, proxied=desired_proxied)
        if created:
            result["created"].append(domain)
        else:
            result["ok"] = False
            result["errors"].append(f"{domain}: {msg}")

    return result
