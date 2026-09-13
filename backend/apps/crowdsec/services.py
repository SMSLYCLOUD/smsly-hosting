from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


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
    raw: Optional[dict] = None


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
        self._cache_ts: float = 0
        self._cache_ttl = 10  # seconds

    def _run_cscli(self, args: list[str]) -> dict:
        """Run cscli and return parsed JSON."""
        cmd = ["docker", "exec", "smsly-crowdsec", "cscli"] + args + ["-o", "json"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                logger.warning("cscli %s failed: %s", " ".join(args), result.stderr)
                return {}
            return json.loads(result.stdout) if result.stdout.strip() else {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
            logger.warning("cscli %s error: %s", " ".join(args), exc)
            return {}

    def _fetch_decisions_raw(self) -> list[dict]:
        return self._run_cscli(["decisions", "list", "-o", "json"])

    def _fetch_alerts_raw(self) -> list[dict]:
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
        """Map service_id -> set of hostnames it routes (public_domain, customs, aliases)."""
        from apps.deployments.models import Service
        mapping = {}
        for svc in Service.objects.only("id", "public_domain", "custom_domains", "host_aliases").iterator():
            hosts = set()
            if svc.public_domain:
                hosts.add(svc.public_domain.strip().lower().rstrip("."))
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
        """Map cscli decision JSON to CrowdSecDecision with service enrichment."""
        # cscli decisions list -o json returns list of objects with these fields:
        # {"id": "...", "type": "ban", "scope": "Ip", "value": "1.2.3.4", 
        #  "origin": "crowdsec", "scenario": "http-probing", "scenario_version": "1.0",
        #  "events_count": 5, "simulated": false, 
        #  "start_time": "2026-09-13T00:00:00Z", "end_time": "2026-09-13T04:00:00Z"}
        
        alert = raw.get("alert", {}) if isinstance(raw.get("alert"), dict) else {}
        scenario = raw.get("scenario") or alert.get("scenario") or ""
        
        # Get host from alert events if available
        service = None
        host = None
        events = alert.get("events", []) if isinstance(alert, dict) else raw.get("events", [])
        if isinstance(events, list) and events:
            first_event = events[0] if isinstance(events[0], dict) else {}
            meta = first_event.get("meta", {}) if isinstance(first_event.get("meta"), dict) else {}
            host = (first_event.get("meta", {}).get("http_host") 
                    or meta.get("http_host") 
                    or meta.get("request_host")
                    or meta.get("host"))
            if host:
                # Try to get service from host
                # Will resolve in batch below
                pass
        
        decision = CrowdSecDecision(
            id=str(raw.get("id") or raw.get("uuid") or ""),
            scope=raw.get("scope", ""),
            value=raw.get("value", ""),
            type=raw.get("type", ""),
            origin=raw.get("origin", ""),
            scenario=scenario,
            scenario_version=raw.get("scenario_version", ""),
            events_count=int(raw.get("events_count") or 0),
            simulated=bool(raw.get("simulated", False)),
            start_time=raw.get("start_time", ""),
            end_time=raw.get("end_time", ""),
            raw=raw,
        )
        # Set service and host for later enrichment
        decision.service = None  # Will be filled in batch
        decision.raw = {"host": host} if host else None
        return decision

    def _normalize_alert(self, raw: dict, host_map: dict[str, set[str]]) -> CrowdSecAlert:
        """Map cscli alert JSON to CrowdSecAlert with service enrichment."""
        alert = raw if isinstance(raw, dict) else {}
        events = alert.get("events", []) if isinstance(alert.get("events"), list) else []
        
        # Extract host from events
        host = None
        events_summary = []
        for ev in events:
            if isinstance(ev, dict):
                meta = ev.get("meta", {}) if isinstance(ev.get("meta"), dict) else {}
                host = ev.get("meta", {}).get("http_host") or meta.get("http_host") or meta.get("request_host")
                if host:
                    break
                events_summary.append({
                    "source": ev.get("source", ""),
                    "method": meta.get("http_method", ""),
                    "path": meta.get("http_path", "") or meta.get("uri", ""),
                    "status": meta.get("http_status", ""),
                    "user_agent": meta.get("http_user_agent", ""),
                })
        
        service = None
        if host:
            service = self._resolve_service_for_host(host, host_map)
        
        return CrowdSecAlert(
            id=str(alert.get("id") or alert.get("uuid") or ""),
            source=alert.get("source", ""),
            scenario=alert.get("scenario", ""),
            scenario_version=alert.get("scenario_version", ""),
            scope=alert.get("scope", ""),
            value=alert.get("value", ""),
            events_count=int(alert.get("events_count") or 0),
            start_time=alert.get("start_time", ""),
            created_at=alert.get("created_at", ""),
            message=alert.get("message", "") or alert.get("description", ""),
            events=events_summary,
            service=service,
        )

    def _enrich_with_service(self, decisions: list[CrowdSecDecision]) -> list[CrowdSecDecision]:
        """Batch-enrich decisions with service_id via host matching."""
        host_map = self._get_service_hosts()
        for d in decisions:
            host = None
            if d.raw and "host" in d.raw:
                host = d.raw.get("host")
            if not host and d.scenario:
                # Try to extract from raw if available
                pass
            if host:
                svc_id = self._resolve_service_for_host(host, host_map)
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
        # Cache
        now = time.time()
        if self._decisions_cache is None or (now - self._cache_ts) > self._cache_ttl:
            raw = self._fetch_decisions_raw()
            self._decisions_cache = self._enrich_with_service(
                [self._normalize_decision(d, self._get_service_hosts()) for d in raw]
            )
            self._cache_ts = now
        
        results = self._decisions_cache
        if active:
            now_iso = datetime.now(timezone.utc).isoformat()
            results = [d for d in results if d.end_time and d.end_time >= now_iso]
        if ip:
            results = [d for d in results if d.value == ip]
        if scenario:
            results = [d for d in results if d.scenario == scenario]
        if service_id:
            results = [d for d in results if d.service == service_id]
        return results[:limit]

    def get_alerts(self, limit: int = 50) -> list[CrowdSecAlert]:
        now = time.time()
        if self._alerts_cache is None or (now - self._cache_ts) > self._cache_ttl:
            raw = self._fetch_alerts_raw()
            host_map = self._get_service_hosts()
            self._alerts_cache = [self._normalize_alert(a, self._get_service_hosts()) for a in raw]
            self._cache_ts = time.time()
        return self._alerts_cache[:limit]

    def get_metrics(self) -> dict:
        return self._fetch_metrics_raw()

    def unban(self, ip: str, range_type: str = "Ip") -> dict:
        """Unban an IP or range."""
        try:
            if range_type == "Range":
                cmd = ["docker", "exec", "smsly-crowdsec", "cscli", "decisions", "delete", "--range", ip]
            else:
                cmd = ["docker", "exec", "smsly-crowdsec", "cscli", "decisions", "delete", "--ip", ip]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                # Invalidate cache
                self._decisions_cache = None
                self._alerts_cache = None
                self._cache_ts = 0
                return {"status": "removed", "ip": ip}
            return {"error": result.stderr or "unban failed"}
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