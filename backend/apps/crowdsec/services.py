from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO datetime string robustly; return None if unparseable."""
    if not value or not isinstance(value, str):
        return None
    try:
        # cscli emits RFC3339 with 'Z'; datetime.fromisoformat needs '+00:00'.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_go_duration(value: Any) -> Optional[float]:
    """Parse a Go duration string (e.g. '2h34m39s', '45m', '20s') to seconds."""
    if not value or not isinstance(value, str):
        return None
    import re

    total = 0.0
    matched = False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ns|us|ms|s|m|h|d|w)", value):
        matched = True
        factor = {
            "ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1,
            "m": 60, "h": 3600, "d": 86400, "w": 604800,
        }[unit]
        total += float(amount) * factor
    return total if matched else None


def _meta_to_dict(meta: Any) -> dict:
    """Normalize cscli event meta to a plain dict.

    Modern cscli emits ``meta`` as a LIST of ``{"key": ..., "value": ...}``
    pairs; older shapes used a flat dict. Accept both so host/path
    extraction keeps working across CrowdSec versions.
    """
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, list):
        out: dict = {}
        for entry in meta:
            if isinstance(entry, dict) and "key" in entry:
                out[entry.get("key")] = entry.get("value")
        return out
    return {}


def _meta_host(meta: dict) -> Optional[str]:
    """Best-effort attacked-host extraction from a normalized meta dict."""
    for key in ("target_fqdn", "http_host", "request_host", "host"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


@dataclass
class CrowdSecDecision:
    """Normalized CrowdSec decision from cscli JSON."""
    id: str
    scope: str
    value: str
    type: str
    origin: str
    scenario: str
    scenario_version: str
    events_count: int
    simulated: bool
    start_time: str
    end_time: str
    service: Optional[str] = None
    host: Optional[str] = None
    raw: Optional[dict] = None
    # Enriched detail fields (populated when the cscli shape carries them).
    duration: str = ""
    source_ip: str = ""
    country: str = ""
    asn_org: str = ""
    ip_range: str = ""
    target_host: str = ""
    paths: Optional[list] = None
    first_seen: str = ""
    last_seen: str = ""
    message: str = ""


@dataclass
class CrowdSecAlert:
    """Normalized CrowdSec alert from cscli JSON."""
    id: str
    source: str
    scenario: str
    scenario_version: str
    scope: str
    value: str
    events_count: int
    start_time: str
    created_at: str
    message: str
    events: list[dict]
    service: Optional[str] = None


class CrowdSecService:
    """CrowdSec LAPI proxy — wraps cscli and enriches with service correlation."""

    def __init__(self):
        self._decisions_cache: Optional[list[CrowdSecDecision]] = None
        self._alerts_cache: Optional[list[CrowdSecAlert]] = None
        self._decisions_ts: float = 0
        self._alerts_ts: float = 0
        self._cache_ttl = 10  # seconds

    def _run_cscli(self, args: list[str]) -> Any:
        """Run cscli and return parsed JSON (list or dict).

        Callers must include their own ``-o json`` flags; this helper
        never appends output flags so duplicate ``-o json -o json``
        can never reach cscli.
        """
        cmd = ["docker", "exec", "smsly-crowdsec", "cscli"] + args
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                logger.warning("cscli %s failed: %s", " ".join(args), result.stderr)
                return []
            if not result.stdout.strip():
                return []
            return json.loads(result.stdout)
        except FileNotFoundError:
            logger.warning("cscli %s failed: docker CLI not available", " ".join(args))
            return []
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
            logger.warning("cscli %s error: %s", " ".join(args), exc)
            return []

    @staticmethod
    def _as_list(raw: Any) -> list[dict]:
        """Normalize cscli JSON output to a list of dicts.

        cscli normally returns a JSON list, but tolerate wrapper dicts
        (``{"decisions": [...]}``) and return [] for anything else so
        callers never iterate dict keys as if they were records.
        """
        if isinstance(raw, list):
            return [d for d in raw if isinstance(d, dict)]
        if isinstance(raw, dict):
            for key in ("decisions", "alerts", "data", "items"):
                nested = raw.get(key)
                if isinstance(nested, list):
                    return [d for d in nested if isinstance(d, dict)]
            return []
        return []

    def _fetch_decisions_raw(self) -> Any:
        return self._run_cscli(["decisions", "list", "-o", "json"])

    def _fetch_alerts_raw(self) -> Any:
        return self._run_cscli(["alerts", "list", "-o", "json"])

    def _fetch_metrics_raw(self) -> dict:
        try:
            result = subprocess.run(
                ["docker", "exec", "smsly-crowdsec", "curl", "-s", "http://localhost:6060/metrics"],
                capture_output=True, text=True, timeout=10
            )
            return {"raw": result.stdout} if result.returncode == 0 else {}
        except Exception as exc:
            logger.warning("CrowdSec metrics fetch failed: %s", exc)
            return {}

    def _get_service_hosts(self) -> dict[str, set[str]]:
        """Map service_id -> set of hostnames it routes (public, staging, customs, aliases)."""
        try:
            from apps.deployments.models import Service
        except Exception as exc:
            logger.debug("CrowdSec host map unavailable: %s", exc)
            return {}
        mapping: dict[str, set[str]] = {}
        try:
            qs = Service.objects.only(
                "id", "public_domain", "staging_domain", "custom_domains", "host_aliases"
            ).iterator()
        except Exception as exc:
            logger.debug("CrowdSec host map query failed: %s", exc)
            return {}
        for svc in qs:
            hosts = set()
            for attr in ("public_domain", "staging_domain"):
                raw_host = getattr(svc, attr, None)
                if isinstance(raw_host, str) and raw_host.strip():
                    hosts.add(raw_host.strip().lower().rstrip("."))
            for d in svc.custom_domains or []:
                if isinstance(d, str) and d.strip():
                    hosts.add(d.strip().lower().rstrip("."))
            for entry in svc.host_aliases or []:
                if isinstance(entry, dict):
                    h = str(entry.get("host") or "").strip().lower().rstrip(".")
                    if h:
                        hosts.add(h)
            if hosts:
                mapping[str(svc.id)] = hosts
        return mapping

    def _resolve_service_for_host(self, host: str, host_map: dict[str, set[str]]) -> Optional[str]:
        """Return service_id that owns this host, or None."""
        if not host:
            return None
        host = host.strip().lower().rstrip(".")
        for svc_id, hosts in host_map.items():
            if host in hosts:
                return svc_id
        return None

    def _normalize_decision(self, raw: dict, host_map: dict[str, set[str]]) -> CrowdSecDecision:
        """Map cscli decision JSON to CrowdSecDecision with service enrichment.

        Modern ``cscli decisions list -o json`` returns alert-shaped items:
        the ban itself lives in the nested ``decisions[0]`` entry
        (``{id, type, scope, value, origin, scenario, duration, ...}``),
        the attacker sits in the ``source`` dict (``{ip, cn, as_name,
        as_number, range, ...}``), and per-event detail (attacked host as
        ``target_fqdn``, probed ``http_path``) lives in ``events[].meta``
        as a LIST of ``{key, value}`` pairs. Older flat shapes (top-level
        ``value``/``type``/dict ``meta``) are still accepted as fallback.
        """
        inner: dict = {}
        nested = raw.get("decisions")
        if isinstance(nested, list):
            for entry in nested:
                if isinstance(entry, dict):
                    inner = entry
                    break

        value = inner.get("value") or raw.get("value") or ""
        type_ = inner.get("type") or raw.get("type") or ""
        scope = inner.get("scope") or raw.get("scope") or ""
        origin = inner.get("origin") or raw.get("origin") or ""
        scenario = inner.get("scenario") or raw.get("scenario") or ""
        alert = raw.get("alert", {}) if isinstance(raw.get("alert"), dict) else {}
        if not scenario and isinstance(alert, dict):
            scenario = alert.get("scenario") or ""
        simulated = inner.get("simulated", raw.get("simulated", False))
        duration = inner.get("duration") or ""
        if not isinstance(duration, str):
            duration = ""

        events = raw.get("events", [])
        if not isinstance(events, list):
            events = []
        metas = [_meta_to_dict(ev.get("meta")) for ev in events if isinstance(ev, dict)]

        host = None
        for meta in metas:
            host = _meta_host(meta)
            if host:
                break
        if not host:
            # Legacy flat shape: host carried beside the decision itself.
            host = _meta_host(raw if isinstance(raw, dict) else {})

        paths: list[str] = []
        for meta in metas:
            path = meta.get("http_path") or meta.get("uri") or ""
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
            if len(paths) >= 8:
                break

        stamps = [
            ev.get("timestamp") for ev in events
            if isinstance(ev, dict) and isinstance(ev.get("timestamp"), str)
        ]
        first_seen = min(stamps) if stamps else ""
        last_seen = max(stamps) if stamps else ""
        start_time = raw.get("start_at") or raw.get("created_at") or raw.get("start_time") or ""
        if not isinstance(start_time, str):
            start_time = ""
        if not first_seen:
            first_seen = start_time

        end_time = raw.get("end_time") or ""
        if not isinstance(end_time, str):
            end_time = ""
        if not end_time:
            # NOTE: the top-level `stop_at` is the *alert bucket* end, not
            # the ban expiry — using it wrongly expires live bans. The
            # nested decision `duration` counts DOWN the remaining ban TTL,
            # so expiry is anchored at fetch time, not at created_at.
            seconds = _parse_go_duration(duration)
            if seconds:
                from datetime import timedelta

                end_time = (
                    datetime.now(timezone.utc) + timedelta(seconds=seconds)
                ).isoformat()

        source = raw.get("source")
        source = source if isinstance(source, dict) else {}
        source_ip = source.get("ip") or ""
        if not isinstance(source_ip, str):
            source_ip = ""
        if not source_ip:
            for meta in metas:
                candidate = meta.get("source_ip") or ""
                if isinstance(candidate, str) and candidate.strip():
                    source_ip = candidate.strip()
                    break
        def _s(val: Any) -> str:
            return val if isinstance(val, str) else ""

        country = _s(source.get("cn") or source.get("IsoCode"))
        asn_org = _s(source.get("as_name") or source.get("ASNOrg"))
        ip_range = _s(source.get("range") or source.get("SourceRange"))

        events_count = raw.get("events_count") or 0
        try:
            events_count = int(events_count)
        except (TypeError, ValueError):
            events_count = len(events)

        message = raw.get("message") or ""
        if not isinstance(message, str):
            message = ""

        return CrowdSecDecision(
            id=str(inner.get("id") or raw.get("id") or raw.get("uuid") or ""),
            scope=scope if isinstance(scope, str) else "",
            value=value if isinstance(value, str) else "",
            type=type_ if isinstance(type_, str) else "",
            origin=origin if isinstance(origin, str) else "",
            scenario=scenario if isinstance(scenario, str) else "",
            scenario_version=raw.get("scenario_version", "") if isinstance(raw.get("scenario_version", ""), str) else "",
            events_count=events_count,
            simulated=bool(simulated),
            start_time=start_time,
            end_time=end_time,
            service=None,  # filled in batch by _enrich_with_service
            host=host,
            raw=raw,
            duration=duration,
            source_ip=source_ip,
            country=country if isinstance(country, str) else "",
            asn_org=asn_org if isinstance(asn_org, str) else "",
            ip_range=ip_range if isinstance(ip_range, str) else "",
            target_host=host or "",
            paths=paths,
            first_seen=first_seen if isinstance(first_seen, str) else "",
            last_seen=last_seen if isinstance(last_seen, str) else "",
            message=message,
        )
    def _normalize_alert(self, raw: dict, host_map: dict[str, set[str]]) -> CrowdSecAlert:
        """Map cscli alert JSON to CrowdSecAlert with service enrichment.

        Accepts the same modern shape as decisions (event ``meta`` as a
        LIST of ``{key, value}`` pairs, attacked host as ``target_fqdn``,
        attacker in the ``source`` dict) as well as older flat shapes.
        """
        alert = raw if isinstance(raw, dict) else {}
        events = alert.get("events", []) if isinstance(alert.get("events"), list) else []

        # Extract host from events; summarize every event (the event
        # carrying the host must not be dropped from the summary).
        host = None
        events_summary = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            meta = _meta_to_dict(ev.get("meta"))
            if host is None:
                host = _meta_host(meta)
            events_summary.append({
                "source": meta.get("source_ip", "") or ev.get("source", ""),
                "method": meta.get("http_verb", "") or meta.get("http_method", ""),
                "path": meta.get("http_path", "") or meta.get("uri", ""),
                "status": meta.get("http_status", ""),
                "user_agent": meta.get("http_user_agent", ""),
            })

        service = None
        if host:
            service = self._resolve_service_for_host(host, host_map)

        source = alert.get("source", "")
        if isinstance(source, dict):
            source = source.get("ip") or ""
        if not isinstance(source, str):
            source = ""

        return CrowdSecAlert(
            id=str(alert.get("id") or alert.get("uuid") or ""),
            source=source,
            scenario=alert.get("scenario", "") if isinstance(alert.get("scenario", ""), str) else "",
            scenario_version=alert.get("scenario_version", "") if isinstance(alert.get("scenario_version", ""), str) else "",
            scope=alert.get("scope", "") if isinstance(alert.get("scope", ""), str) else "",
            value=alert.get("value", "") if isinstance(alert.get("value", ""), str) else "",
            events_count=int(alert.get("events_count") or 0),
            start_time=alert.get("start_at") or alert.get("start_time") or "",
            created_at=alert.get("created_at", "") if isinstance(alert.get("created_at", ""), str) else "",
            message=alert.get("message", "") or alert.get("description", ""),
            events=events_summary,
            service=service,
        )

    def _enrich_with_service(self, decisions: list[CrowdSecDecision]) -> list[CrowdSecDecision]:
        """Batch-enrich decisions with service_id via host matching."""
        host_map = self._get_service_hosts()
        for d in decisions:
            if d.host:
                svc_id = self._resolve_service_for_host(d.host, host_map)
                if svc_id:
                    d.service = svc_id
        return decisions

    def get_decisions(
        self,
        active: bool = True,
        ip: Optional[str] = None,
        scenario: Optional[str] = None,
        service_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[CrowdSecDecision]:
        """Get decisions with optional filters, enriched with service_id."""
        # Cache (decisions have their own timestamp; alerts must not evict them).
        now = time.time()
        if self._decisions_cache is None or (now - self._decisions_ts) > self._cache_ttl:
            raw = self._as_list(self._fetch_decisions_raw())
            host_map = self._get_service_hosts()
            self._decisions_cache = self._enrich_with_service(
                [self._normalize_decision(d, host_map) for d in raw]
            )
            self._decisions_ts = now

        results = list(self._decisions_cache)
        if active:
            now_dt = datetime.now(timezone.utc)
            active_results = []
            for d in results:
                end_dt = _parse_dt(d.end_time)
                # Keep decisions with no/unparseable end_time (fail-open:
                # never hide a ban because of a timestamp format change).
                if end_dt is None or end_dt >= now_dt:
                    active_results.append(d)
            results = active_results
        if ip:
            results = [d for d in results if d.value == ip]
        if scenario:
            results = [d for d in results if d.scenario == scenario]
        if service_id:
            results = [d for d in results if d.service == service_id]
        return results[:limit]

    def get_alerts(self, limit: int = 50) -> list[CrowdSecAlert]:
        now = time.time()
        if self._alerts_cache is None or (now - self._alerts_ts) > self._cache_ttl:
            raw = self._as_list(self._fetch_alerts_raw())
            host_map = self._get_service_hosts()
            self._alerts_cache = [self._normalize_alert(a, host_map) for a in raw]
            self._alerts_ts = now
        return list(self._alerts_cache)[:limit]

    def get_metrics(self) -> dict:
        return self._fetch_metrics_raw()

    def unban(self, ip: str, range_type: str = "Ip") -> dict:
        """Unban an IP or range. Returns {"status": ...} or {"error": ...}."""
        try:
            if range_type == "Range":
                ipaddress.ip_network(ip, strict=False)
                cmd = ["docker", "exec", "smsly-crowdsec", "cscli", "decisions", "delete", "--range", ip]
            else:
                ipaddress.ip_address(ip)
                cmd = ["docker", "exec", "smsly-crowdsec", "cscli", "decisions", "delete", "--ip", ip]
        except ValueError:
            return {"error": f"invalid {'CIDR range' if range_type == 'Range' else 'IP address'}: {ip}"}
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                # Invalidate caches
                self._decisions_cache = None
                self._alerts_cache = None
                self._decisions_ts = 0
                self._alerts_ts = 0
                return {"status": "removed", "ip": ip}
            return {"error": result.stderr or "unban failed"}
        except FileNotFoundError:
            logger.warning("unban failed: docker CLI not available")
            return {"error": "docker CLI not available on this host"}
        except Exception as exc:
            logger.exception("unban failed")
            return {"error": str(exc)}

    def get_service_decisions(self, service_id: str, active: bool = True) -> list[CrowdSecDecision]:
        """Convenience: all decisions for a specific service."""
        return self.get_decisions(active=active, service_id=service_id)

    def get_service_alerts(self, service_id: str) -> list[CrowdSecAlert]:
        alerts = self.get_alerts()
        return [a for a in alerts if a.service == service_id]


# Global instance
crowdsec_service = CrowdSecService()


def get_crowdsec_service() -> CrowdSecService:
    return crowdsec_service