#!/usr/bin/env python3
"""
CrowdSec -> Cloudflare Rulesets API v4 Edge Bouncer Daemon.

Replaces the retired upstream v0.3.0 Go binary (which crashes calling
Cloudflare's deprecated /zones/{id}/firewall/rules endpoint with error 10020).
Reads /etc/crowdsec/bouncers/crowdsec-cloudflare-bouncer.yaml, maintains the
account IP list (crowdsec_block) + custom WAF ruleset on every configured zone
using Cloudflare Rulesets API v4, respects capacity and async bulk-op pacing,
and keeps CrowdSec LAPI /v1/decisions/stream continuously fresh.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

CFG_PATH = os.environ.get(
    "CF_BOUNCER_CONFIG",
    "/etc/crowdsec/bouncers/crowdsec-cloudflare-bouncer.yaml",
)
CF_API_BASE = "https://api.cloudflare.com/client/v4"
LAPI_POLL_SECONDS = 2
BULK_BATCH_SIZE = 500
LIST_MIN_CAPI_INTERVAL = 180
LIST_FULL_REFRESH_INTERVAL = 3600
FAST_LANE_DESC = "CrowdSec First-Strike Fast-Lane (local)"
FAST_LANE_MAX_IPS = 80
FAST_LANE_MAX_EXPR_LEN = 3800
RUNNING = True


def _handle_signal(_signum, _frame):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def log(level: str, msg: str, **kw) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    extra = "".join(f" {k}={v}" for k, v in kw.items())
    print(f'time="{ts}" level={level} msg="{msg}"{extra}', flush=True)


def parse_config(path: str) -> dict:
    cfg = {
        "lapi_url": "http://smsly-crowdsec:8080/",
        "lapi_key": "",
        "account_id": "",
        "token": "",
        "prefix": "crowdsec",
        "action": "block",
        "capacity": 10000,
        "zones": [],
    }
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("crowdsec_lapi_url:"):
                cfg["lapi_url"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("crowdsec_lapi_key:"):
                cfg["lapi_key"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("- id:") and not cfg["account_id"]:
                cfg["account_id"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("token:") and not cfg["token"]:
                cfg["token"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("ip_list_prefix:"):
                cfg["prefix"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("default_action:"):
                cfg["action"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("total_ip_list_capacity:"):
                try:
                    cfg["capacity"] = max(1, int(line.split(":", 1)[1].strip()))
                except ValueError:
                    pass
            elif line.startswith("- zone_id:"):
                zid = line.split(":", 1)[1].strip().strip("'\"")
                if zid and zid not in cfg["zones"]:
                    cfg["zones"].append(zid)
    return cfg


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload=None,
    timeout: int = 20,
) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body) if body.strip() else {}


def norm_ip(val: str | None) -> str | None:
    if not val:
        return None
    raw = str(val).strip()
    try:
        if "/" in raw:
            net = ipaddress.ip_network(raw, strict=False)
            if net.version == 4:
                return str(net.network_address) if net.prefixlen == 32 else str(net)
            if net.prefixlen > 64:
                net = ipaddress.ip_network((net.network_address, 64), strict=False)
            return str(net)
        addr = ipaddress.ip_address(raw)
        if addr.version == 4:
            return str(addr)
        return str(ipaddress.ip_network((addr, 64), strict=False))
    except Exception:
        return None


def fetch_all_list_items(
    account_id: str,
    list_id: str,
    cf_headers: dict[str, str],
) -> dict[str, str]:
    known: dict[str, str] = {}
    cursor = ""
    while RUNNING:
        qs = f"?cursor={cursor}" if cursor else ""
        res = http_json(
            f"{CF_API_BASE}/accounts/{account_id}/rules/lists/{list_id}/items{qs}",
            headers=cf_headers,
        )
        for item in res.get("result") or []:
            ip = item.get("ip")
            iid = item.get("id")
            if ip and iid:
                known[ip] = iid
        cursor = ((res.get("result_info") or {}).get("cursors") or {}).get("after") or ""
        if not cursor:
            break
    return known


def await_bulk_operation(
    account_id: str,
    op_id: str,
    cf_headers: dict[str, str],
) -> str:
    if not op_id:
        return "completed"
    for _ in range(30):
        if not RUNNING:
            return "interrupted"
        time.sleep(1)
        res = http_json(
            f"{CF_API_BASE}/accounts/{account_id}/rules/lists/bulk_operations/{op_id}",
            headers=cf_headers,
        )
        status = str((res.get("result") or {}).get("status") or "").lower()
        if status in ("completed", "failed"):
            return status
    return "pending"


def ensure_zone_rulesets(
    zones: list[str],
    list_name: str,
    action: str,
    cf_headers: dict[str, str],
) -> dict[str, dict[str, str | None]]:
    """Ensure the main IP-list rule exists per zone and return zone ruleset state."""
    expr = f"(ip.src in ${list_name})"
    rule_spec = {
        "action": action,
        "expression": expr,
        "description": f"CrowdSec Edge Block List ({list_name})",
        "enabled": True,
    }
    zone_state: dict[str, dict[str, str | None]] = {}
    for zid in zones:
        if not RUNNING:
            break
        try:
            ep = http_json(
                f"{CF_API_BASE}/zones/{zid}/rulesets/phases/http_request_firewall_custom/entrypoint",
                headers=cf_headers,
            )
            rs = ep.get("result") or {}
            rs_id = rs.get("id") or ""
            rules = rs.get("rules") or []
            fast_rule_id = next(
                (r.get("id") for r in rules if r.get("description") == FAST_LANE_DESC and r.get("id")),
                None,
            )
            if not any(list_name in str(r.get("expression") or "") for r in rules) and rs_id:
                http_json(
                    f"{CF_API_BASE}/zones/{zid}/rulesets/{rs_id}/rules",
                    method="POST",
                    headers=cf_headers,
                    payload=rule_spec,
                )
            if rs_id:
                zone_state[zid] = {"ruleset_id": rs_id, "fast_rule_id": fast_rule_id}
        except urllib.error.HTTPError as he:
            if he.code == 404:
                try:
                    created = http_json(
                        f"{CF_API_BASE}/zones/{zid}/rulesets/phases/http_request_firewall_custom/entrypoint",
                        method="PUT",
                        headers=cf_headers,
                        payload={"rules": [rule_spec]},
                    )
                    rs_id = (created.get("result") or {}).get("id") or ""
                    if rs_id:
                        zone_state[zid] = {"ruleset_id": rs_id, "fast_rule_id": None}
                except Exception as exc2:
                    log("warning", f"zone {zid} ruleset init deferred: {exc2}")
            else:
                log("warning", f"zone {zid} ruleset check status={he.code}")
        except Exception as exc:
            log("warning", f"zone {zid} ruleset check skipped: {exc}")
        time.sleep(0.2)
    return zone_state


def build_fast_lane_ips(
    active_local_ips: dict[str, float],
    known_ips: dict[str, str],
) -> tuple[str, ...]:
    """Select newest active local attacker IPs not yet confirmed in crowdsec_block."""
    ordered = [
        ip for ip, _ts in sorted(active_local_ips.items(), key=lambda kv: (-kv[1], kv[0]))
        if ip not in known_ips
    ]
    chosen: list[str] = []
    # "(ip.src in {" + " ".join(chosen) + "})"
    running_len = len("(ip.src in {})")
    for ip in ordered:
        if len(chosen) >= FAST_LANE_MAX_IPS:
            break
        sep = 1 if chosen else 0
        if running_len + sep + len(ip) > FAST_LANE_MAX_EXPR_LEN:
            break
        chosen.append(ip)
        running_len += sep + len(ip)
    return tuple(sorted(chosen))


def sync_fast_lane_rules(
    zone_state: dict[str, dict[str, str | None]],
    ips: tuple[str, ...],
    action: str,
    cf_headers: dict[str, str],
) -> None:
    """Push or clear an inline zone custom WAF rule for urgent local First Strike bans.

    Zone custom rules are outside the /accounts/{id}/rules/lists quota (error 10040),
    keeping edge enforcement < 4s even while CAPI bulk list writes are rate-limited.
    """
    for zid, meta in zone_state.items():
        if not RUNNING:
            break
        rs_id = meta.get("ruleset_id")
        if not rs_id:
            continue
        fast_rule_id = meta.get("fast_rule_id")
        if ips:
            payload = {
                "action": action,
                "expression": f"(ip.src in {{{' '.join(ips)}}})",
                "description": FAST_LANE_DESC,
                "enabled": True,
            }
            if fast_rule_id:
                try:
                    http_json(
                        f"{CF_API_BASE}/zones/{zid}/rulesets/{rs_id}/rules/{fast_rule_id}",
                        method="PATCH",
                        headers=cf_headers,
                        payload=payload,
                    )
                    continue
                except urllib.error.HTTPError as he:
                    if he.code != 404:
                        raise
                    meta["fast_rule_id"] = None
            resp = http_json(
                f"{CF_API_BASE}/zones/{zid}/rulesets/{rs_id}/rules",
                method="POST",
                headers=cf_headers,
                payload=payload,
            )
            rules = (resp.get("result") or {}).get("rules") or []
            meta["fast_rule_id"] = next(
                (r.get("id") for r in rules if r.get("description") == FAST_LANE_DESC and r.get("id")),
                None,
            )
        elif fast_rule_id:
            try:
                http_json(
                    f"{CF_API_BASE}/zones/{zid}/rulesets/{rs_id}/rules/{fast_rule_id}",
                    method="DELETE",
                    headers=cf_headers,
                )
            except urllib.error.HTTPError as he:
                if he.code != 404:
                    raise
            meta["fast_rule_id"] = None
        time.sleep(0.05)


def _extract_rate_limit_wait(he: urllib.error.HTTPError, default_wait: int) -> int:
    retry_hdr = he.headers.get("Retry-After") if he.headers else None
    try:
        hdr_sec = int(float(retry_hdr)) if retry_hdr else 0
    except ValueError:
        hdr_sec = 0
    return max(default_wait, hdr_sec, 120)


def main() -> int:
    if not os.path.exists(CFG_PATH) or os.path.getsize(CFG_PATH) == 0:
        print("[cf-bouncer] no config - idle (enable in Settings > Security)", flush=True)
        while RUNNING:
            time.sleep(60)
        return 0

    cfg = parse_config(CFG_PATH)
    account_id = cfg["account_id"]
    token = cfg["token"]
    lapi_url = cfg["lapi_url"].rstrip("/")
    lapi_key = cfg["lapi_key"]
    prefix = cfg["prefix"]
    action = cfg["action"]
    capacity = cfg["capacity"]
    zones = cfg["zones"]
    list_name = f"{prefix}_{action}"

    if not account_id or not token or not lapi_key:
        log("error", "missing required account_id, token, or lapi_key in bouncer config")
        return 1

    log("info", "Starting crowdsec-cloudflare-bouncer (Rulesets API v4)")
    log("info", "Using API key auth")

    cf_headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "smsly-crowdsec-cf-bouncer/1.0",
    }
    lapi_headers = {
        "X-Api-Key": lapi_key,
        "User-Agent": "crowdsec-cloudflare-bouncer/v0.3.0-rulesets",
    }

    list_id = ""
    while RUNNING and not list_id:
        try:
            res = http_json(f"{CF_API_BASE}/accounts/{account_id}/rules/lists", headers=cf_headers)
            for item in res.get("result") or []:
                if item.get("name") == list_name:
                    list_id = item.get("id") or ""
                    break
            if not list_id:
                created = http_json(
                    f"{CF_API_BASE}/accounts/{account_id}/rules/lists",
                    method="POST",
                    headers=cf_headers,
                    payload={
                        "name": list_name,
                        "kind": "ip",
                        "description": "CrowdSec IP blocklist",
                    },
                )
                list_id = (created.get("result") or {}).get("id") or ""
            if list_id:
                log("info", f"using existing ip list {list_name} {list_id}", account_id=account_id)
        except urllib.error.HTTPError as he:
            body = he.read().decode("utf-8", errors="replace")[:180]
            if he.code in (401, 403):
                log("error", f"cloudflare auth rejected (status={he.code}): {body}")
                time.sleep(60)
            else:
                log("warning", f"cloudflare list lookup retry status={he.code}: {body}")
                time.sleep(15)
        except Exception as exc:
            log("warning", f"waiting for cloudflare list lookup: {exc}")
            time.sleep(15)

    if not RUNNING:
        return 0

    zone_state = ensure_zone_rulesets(zones, list_name, action, cf_headers)
    protected_zones = len(zone_state)
    known_ips: dict[str, str] = {}
    try:
        known_ips = fetch_all_list_items(account_id, list_id, cf_headers)
    except Exception as exc:
        log("warning", f"initial list item read skipped: {exc}")

    log(
        "info",
        "setup of firewall rules complete",
        account_id=account_id,
        list_id=list_id,
        zones=protected_zones,
        synced_ips=len(known_ips),
    )

    # Buffered delta state survives temporary Cloudflare 429 cooldowns without
    # losing decisions or hammering the bulk write endpoint.
    # priority: 0 = local CrowdSec/cscli decision (top priority), 1 = CAPI/lists feed
    pending_adds: dict[str, tuple[int, str]] = {}
    pending_dels: set[str] = set()
    active_local_ips: dict[str, float] = {}
    list_cooldown_until = 0.0
    fast_lane_cooldown_until = 0.0
    cf_backoff_sec = 120
    last_capi_write_ts = 0.0
    last_list_refresh_ts = time.time()
    last_fast_lane: tuple[str, ...] = ()
    startup = "true"
    last_status_log = 0.0

    while RUNNING:
        # 1. Always keep CrowdSec LAPI stream fresh every cycle.
        now = time.time()
        try:
            stream = http_json(
                f"{lapi_url}/v1/decisions/stream?startup={startup}",
                headers=lapi_headers,
                timeout=20,
            )
            startup = "false"

            for dec in stream.get("deleted") or []:
                nip = norm_ip(dec.get("value"))
                if not nip:
                    continue
                active_local_ips.pop(nip, None)
                pending_adds.pop(nip, None)
                if nip in known_ips:
                    pending_dels.add(nip)

            for dec in stream.get("new") or []:
                nip = norm_ip(dec.get("value"))
                if not nip:
                    continue
                pending_dels.discard(nip)
                orig = str(dec.get("origin") or "crowdsec").strip().lower()
                is_local = orig not in ("capi", "lists")
                if is_local:
                    active_local_ips[nip] = now
                if nip not in known_ips:
                    prio = 0 if is_local else 1
                    comment = f"crowdsec:{dec.get('scenario', 'ban')}"[:100]
                    prev = pending_adds.get(nip)
                    if prev is None or prio < prev[0]:
                        pending_adds[nip] = (prio, comment)
        except urllib.error.HTTPError as he:
            body = he.read().decode("utf-8", errors="replace")[:160]
            if he.code in (401, 403):
                log("error", f"crowdsec LAPI auth failure status={he.code}: {body}")
            else:
                log("warning", f"crowdsec LAPI poll transient status={he.code}: {body}")
        except Exception as exc:
            log("warning", f"crowdsec LAPI poll transient: {exc}")

        # 2. Keep Cloudflare zone fast-lane inline WAF rule synced for local First-Strike bans.
        now = time.time()
        desired_fast_lane = build_fast_lane_ips(active_local_ips, known_ips)
        if desired_fast_lane != last_fast_lane and now >= fast_lane_cooldown_until:
            try:
                sync_fast_lane_rules(zone_state, desired_fast_lane, action, cf_headers)
                last_fast_lane = desired_fast_lane
                if desired_fast_lane:
                    log(
                        "info",
                        f"synced {len(desired_fast_lane)} local first-strike IPs to Cloudflare fast-lane rule",
                        zones=protected_zones,
                    )
                else:
                    log("info", "cleared Cloudflare fast-lane rule", zones=protected_zones)
            except urllib.error.HTTPError as he:
                wait_sec = _extract_rate_limit_wait(he, 60) if he.code == 429 else 30
                fast_lane_cooldown_until = time.time() + wait_sec
                log("warning", f"fast-lane zone sync transient status={he.code} wait={wait_sec}s")
            except Exception as exc:
                fast_lane_cooldown_until = time.time() + 30
                log("warning", f"fast-lane zone sync transient: {exc}")

        # 3. Flush pending account IP-list changes outside list cooldown, pacing CAPI bulk writes.
        now = time.time()
        if now - last_list_refresh_ts >= LIST_FULL_REFRESH_INTERVAL:
            try:
                known_ips = fetch_all_list_items(account_id, list_id, cf_headers)
                last_list_refresh_ts = now
            except Exception as exc:
                log("warning", f"periodic list refresh skipped: {exc}")

        has_local_adds = any(v[0] == 0 for v in pending_adds.values())
        capi_due = bool(pending_adds) and (now - last_capi_write_ts >= LIST_MIN_CAPI_INTERVAL)
        if (pending_dels or has_local_adds or capi_due) and now >= list_cooldown_until:
            try:
                if pending_dels:
                    # Resolve real Cloudflare item IDs if any target was tracked optimistically.
                    if any(ip in known_ips and not known_ips[ip] for ip in pending_dels):
                        known_ips = fetch_all_list_items(account_id, list_id, cf_headers)
                        last_list_refresh_ts = time.time()
                    del_items = []
                    flushed_ips = []
                    for ip in list(pending_dels)[:BULK_BATCH_SIZE]:
                        iid = known_ips.get(ip)
                        if iid:
                            del_items.append({"id": iid})
                            flushed_ips.append(ip)
                        else:
                            known_ips.pop(ip, None)
                            pending_dels.discard(ip)
                    if del_items:
                        resp = http_json(
                            f"{CF_API_BASE}/accounts/{account_id}/rules/lists/{list_id}/items",
                            method="DELETE",
                            headers=cf_headers,
                            payload={"items": del_items},
                            timeout=30,
                        )
                        op_id = (resp.get("result") or {}).get("operation_id") or ""
                        await_bulk_operation(account_id, op_id, cf_headers)
                        for ip in flushed_ips:
                            known_ips.pop(ip, None)
                            pending_dels.discard(ip)
                        cf_backoff_sec = 120
                        log("info", f"removed {len(flushed_ips)} expired ip bans from {list_name}", synced_ips=len(known_ips))

                slots = max(0, capacity - len(known_ips))
                if (has_local_adds or capi_due) and slots > 0:
                    ordered = sorted(pending_adds.items(), key=lambda kv: (kv[1][0], kv[0]))
                    batch_entries = ordered[: min(slots, BULK_BATCH_SIZE)]
                    if batch_entries:
                        payload = [{"ip": ip, "comment": meta[1]} for ip, meta in batch_entries]
                        resp = http_json(
                            f"{CF_API_BASE}/accounts/{account_id}/rules/lists/{list_id}/items",
                            method="POST",
                            headers=cf_headers,
                            payload=payload,
                            timeout=45,
                        )
                        op_id = (resp.get("result") or {}).get("operation_id") or ""
                        op_status = await_bulk_operation(account_id, op_id, cf_headers)
                        if any(meta[0] > 0 for _, meta in batch_entries):
                            last_capi_write_ts = time.time()
                        if op_status == "completed":
                            for ip, _meta in batch_entries:
                                known_ips.setdefault(ip, "")
                                pending_adds.pop(ip, None)
                        else:
                            known_ips = fetch_all_list_items(account_id, list_id, cf_headers)
                            last_list_refresh_ts = time.time()
                            for ip, _meta in batch_entries:
                                if ip in known_ips:
                                    pending_adds.pop(ip, None)
                        cf_backoff_sec = 120
                        log(
                            "info",
                            f"synced {len(batch_entries)} ip bans to {list_name}",
                            synced_ips=len(known_ips),
                            pending_sync=len(pending_adds),
                            op_status=op_status,
                        )
                elif pending_adds and slots == 0:
                    # Respect Cloudflare plan capacity; trim low-priority CAPI queue.
                    pending_adds = {k: v for k, v in pending_adds.items() if v[0] == 0}
            except urllib.error.HTTPError as he:
                body = he.read().decode("utf-8", errors="replace")[:200]
                if he.code == 429:
                    wait_sec = _extract_rate_limit_wait(he, cf_backoff_sec)
                    list_cooldown_until = time.time() + wait_sec
                    cf_backoff_sec = min(1800, max(300, cf_backoff_sec * 2))
                    log(
                        "warning",
                        f"cloudflare bulk write rate-limited (status 429); cooling down {wait_sec}s (fast-lane active for {len(last_fast_lane)} local IPs)",
                        synced_ips=len(known_ips),
                        pending_sync=len(pending_adds),
                        zones=protected_zones,
                    )
                elif he.code in (401, 403):
                    log("error", f"cloudflare token unauthorized status={he.code}: {body}")
                    list_cooldown_until = time.time() + 300
                elif re.search(r"exceed|maximum|limit|capacity", body, re.I):
                    log("warning", f"cloudflare list capacity reached ({len(known_ips)} items): {body}")
                    pending_adds = {k: v for k, v in pending_adds.items() if v[0] == 0}
                    list_cooldown_until = time.time() + 300
                else:
                    log("warning", f"cloudflare list sync transient status={he.code}: {body}")
                    list_cooldown_until = time.time() + 60
            except Exception as exc:
                log("warning", f"cloudflare list sync transient: {exc}")
                list_cooldown_until = time.time() + 60

        if now - last_status_log >= 300:
            last_status_log = now
            log(
                "info",
                "cf_bouncer heartbeat",
                list_id=list_id,
                synced_ips=len(known_ips),
                pending_sync=len(pending_adds),
                fast_lane_ips=len(last_fast_lane),
                zones=protected_zones,
            )

        for _ in range(LAPI_POLL_SECONDS):
            if not RUNNING:
                break
            time.sleep(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
