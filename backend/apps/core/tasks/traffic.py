"""Celery tasks for collecting Traefik access log traffic and resolving
IP geolocations asynchronously (non-blocking)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests
from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM, TASK_TIME_LIMIT_STANDARD
from django.db import IntegrityError
from django.db.models import F

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ACCESS_LOG_CANDIDATES = [
    Path('/var/log/caddy/access.log'),
    Path('/var/log/traefik/access.log'),
]
OFFSET_FILE = Path('/tmp/.traefik_log_offset')

SKIP_PATHS = frozenset({'/ping', '/health', '/health/live', '/health/ready', '/metrics'})

IP_API_URL = 'http://ip-api.com/json/{ip}?fields=status,countryCode,country,city,lat,lon'
IP_API_DELAY = 1.4  # 45 req/min -> ~1.33s; use 1.4s for safety margin

# Cached toggle state to avoid hitting DB every 15s/30s
_traffic_geo_cache: tuple = (0.0, True)
_TRAFFIC_GEO_CACHE_TTL = 60  # seconds


def _is_traffic_geo_enabled() -> bool:
    """Return cached traffic_geo_enabled value, refreshing every 60s."""
    global _traffic_geo_cache
    now = time.time()
    if now - _traffic_geo_cache[0] < _TRAFFIC_GEO_CACHE_TTL:
        return _traffic_geo_cache[1]
    try:
        from apps.deployments.models.core import PlatformConfig
        enabled = PlatformConfig.load().traffic_geo_enabled
    except Exception:
        enabled = True  # fail-open: keep collecting if DB is down
    _traffic_geo_cache = (now, enabled)
    return enabled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_active_access_log() -> Path | None:
    import os
    env_path = os.environ.get("TRAFFIC_ACCESS_LOG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    for path in ACCESS_LOG_CANDIDATES:
        if path.exists():
            return path
    return None


def _read_offset(log_path: Path) -> int:
    offset_file = Path(f"/tmp/.traffic_log_offset_{abs(hash(str(log_path)))}")
    try:
        return int(offset_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_offset(log_path: Path, pos: int) -> None:
    offset_file = Path(f"/tmp/.traffic_log_offset_{abs(hash(str(log_path)))}")
    offset_file.write_text(str(pos))


def _is_private_ip(ip: str) -> bool:
    """Check if IP is private/loopback (handles IPv4 and IPv6)."""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _clean_domain(domain: str) -> str:
    domain = (domain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    if "/" in domain:
        domain = domain.split("/")[0]
    if ":" in domain:
        domain = domain.split(":")[0]
    return domain


def _upsert_traffic_row(ip: str, domain: str) -> None:
    """Insert or increment traffic count for an IP+domain combination."""
    from apps.deployments.models import Service
    from apps.deployments.models.traffic import ServiceTrafficLog

    clean_domain = _clean_domain(domain)
    if not clean_domain:
        return

    service = Service.objects.filter(public_domain__iexact=clean_domain).first()
    if not service:
        service = Service.objects.filter(custom_domains__contains=[clean_domain]).first()
    if not service:
        for candidate in Service.objects.filter(public_domain__gt=''):
            if _clean_domain(candidate.public_domain) == clean_domain:
                service = candidate
                break
    if not service:
        return

    is_private = _is_private_ip(ip)
    defaults = {
        'request_count': 1,
        'geo_resolved': is_private,
        'country_code': 'LN' if is_private else '',
        'country_name': 'Local Network' if is_private else '',
        'city': 'Internal' if is_private else '',
        'latitude': 0.0 if is_private else None,
        'longitude': 0.0 if is_private else None,
    }

    try:
        log_entry, created = ServiceTrafficLog.objects.get_or_create(
            service=service,
            ip_address=ip,
            domain=clean_domain,
            defaults=defaults,
        )
        if not created:
            ServiceTrafficLog.objects.filter(pk=log_entry.pk).update(
                request_count=F('request_count') + 1
            )
    except IntegrityError:
        pass


def _extract_log_fields(entry: dict) -> tuple[str, str, str, int]:
    """Parse client_ip, request_host, request_uri, and status from Caddy/Traefik JSON logs."""
    request_host = ""
    req = entry.get("request", {})
    if isinstance(req, dict):
        request_host = req.get("host", "")
    if not request_host:
        request_host = entry.get("RequestHost", "") or entry.get("host", "")

    client_ip = ""
    if isinstance(req, dict):
        headers = req.get("headers", {})
        if isinstance(headers, dict):
            xff = headers.get("X-Forwarded-For") or headers.get("x-forwarded-for")
            if isinstance(xff, list) and xff:
                client_ip = str(xff[0]).split(",")[0].strip()
            elif isinstance(xff, str) and xff:
                client_ip = xff.split(",")[0].strip()
        if not client_ip:
            client_ip = req.get("client_ip", "") or req.get("remote_ip", "")
    if not client_ip:
        client_ip = entry.get("ClientHost", "") or entry.get("client_ip", "") or entry.get("remote_ip", "")

    if client_ip and client_ip.count(".") == 3 and ":" in client_ip:
        client_ip = client_ip.split(":")[0]

    request_uri = ""
    if isinstance(req, dict):
        request_uri = req.get("uri", "")
    if not request_uri:
        request_uri = entry.get("RequestURI", "") or entry.get("uri", "")

    status = entry.get("status", 0)
    if not status:
        status = entry.get("DownstreamStatus", 0)
    try:
        status = int(status)
    except (TypeError, ValueError):
        status = 0

    return client_ip.strip(), request_host.strip(), request_uri.strip(), status


# ---------------------------------------------------------------------------
# Task 1: Collect Traefik / Caddy access log entries
# ---------------------------------------------------------------------------
@shared_task(bind=True, ignore_result=True, max_retries=2, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def collect_traefik_logs(self) -> None:
    """Tail Traefik / Caddy access.log (JSON format), map RequestHost -> Service,
    and upsert ServiceTrafficLog rows. Runs every ~15 seconds."""
    if not _is_traffic_geo_enabled():
        return
    log_path = _get_active_access_log()
    if not log_path:
        return

    offset = _read_offset(log_path)
    file_size = log_path.stat().st_size

    if file_size < offset:
        offset = 0

    if offset == file_size:
        return

    try:
        with open(log_path, 'r', buffering=8192) as fh:
            fh.seek(offset)
            new_lines = 0
            while True:
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                client_ip, request_host, request_uri, status = _extract_log_fields(entry)
                if not client_ip or not request_host:
                    continue

                path = request_uri.split('?')[0] if request_uri else ''
                if path in SKIP_PATHS:
                    continue

                if status >= 400:
                    continue

                _upsert_traffic_row(client_ip, request_host)
                new_lines += 1

                if new_lines % 100 == 0:
                    _write_offset(log_path, fh.tell())

            _write_offset(log_path, fh.tell())

        if new_lines:
            logger.debug("Access log collector: processed %d new entries", new_lines)

    except Exception as exc:
        logger.warning("Access log collector error: %s", exc, exc_info=True)
        raise self.retry(countdown=5, exc=exc)


# ---------------------------------------------------------------------------
# Task 2: Resolve IP geolocations asynchronously
# ---------------------------------------------------------------------------
@shared_task(bind=True, ignore_result=True, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def resolve_traffic_geolocations(self) -> None:
    """Batch-resolve unresolved IPs via ip-api.com. Rate-limited to 45 req/min.
    Runs every ~30 seconds, processes up to 20 IPs per batch."""
    if not _is_traffic_geo_enabled():
        return
    from apps.deployments.models.traffic import ServiceTrafficLog

    unresolved = list(
        ServiceTrafficLog.objects.filter(geo_resolved=False)
        .order_by('created_at')[:20]
    )
    if not unresolved:
        return

    resolved_count = 0
    for i, log_entry in enumerate(unresolved):
        ip = log_entry.ip_address
        try:
            resp = requests.get(
                IP_API_URL.format(ip=ip),
                timeout=5,
                headers={'User-Agent': 'smsly-hosting/1.0'},
            )
            data = resp.json()

            if data.get('status') == 'success':
                log_entry.country_code = data.get('countryCode', '')
                log_entry.country_name = data.get('country', '')
                log_entry.city = data.get('city', '')
                log_entry.latitude = data.get('lat')
                log_entry.longitude = data.get('lon')
                log_entry.geo_resolved = True
                log_entry.save(update_fields=[
                    'country_code', 'country_name', 'city',
                    'latitude', 'longitude', 'geo_resolved',
                ])
                resolved_count += 1
            else:
                log_entry.geo_resolved = True
                log_entry.save(update_fields=['geo_resolved'])

        except (requests.RequestException, ValueError) as exc:
            logger.warning("Geo lookup failed for %s: %s", ip, exc)

        if i < len(unresolved) - 1:
            time.sleep(IP_API_DELAY)

    if resolved_count:
        logger.debug("Geo resolver: resolved %d IPs", resolved_count)
