"""Celery tasks for collecting Traefik access log traffic and resolving
IP geolocations asynchronously (non-blocking)."""
import json
import logging
import time
from pathlib import Path

import requests
from celery import shared_task
from django.db import IntegrityError
from django.db.models import F

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ACCESS_LOG = Path('/var/log/traefik/access.log')
OFFSET_FILE = Path('/tmp/.traefik_log_offset')

SKIP_PATHS = frozenset({'/ping', '/health', '/health/live', '/health/ready', '/metrics'})

IP_API_URL = 'http://ip-api.com/json/{ip}?fields=status,countryCode,country,city,lat,lon'
IP_API_DELAY = 1.4  # 45 req/min -> ~1.33s; use 1.4s for safety margin


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_offset(pos: int) -> None:
    OFFSET_FILE.write_text(str(pos))


def _is_private_ip(ip: str) -> bool:
    """Check if IP is private/loopback (handles IPv4 and IPv6)."""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return addr.is_private or addr.is_loopback or addr.is_link_local


def _upsert_traffic_row(ip: str, domain: str) -> None:
    """Insert or increment traffic count for an IP+domain combination."""
    from .models import Service
    from .models_traffic import ServiceTrafficLog

    if not domain:
        return

    service = Service.objects.filter(public_domain__iexact=domain).first()
    if not service:
        service = Service.objects.filter(custom_domains__contains=[domain]).first()
    if not service:
        return

    try:
        ServiceTrafficLog.objects.update_or_create(
            service=service,
            ip_address=ip,
            domain=domain,
            defaults={'request_count': F('request_count') + 1},
        )
    except IntegrityError:
        pass


# ---------------------------------------------------------------------------
# Task 1: Collect Traefik access log entries
# ---------------------------------------------------------------------------
@shared_task(bind=True, ignore_result=True, max_retries=2)
def collect_traefik_logs(self):
    """Tail Traefik access.log (JSON format), map RequestHost -> Service,
    and upsert ServiceTrafficLog rows. Runs every ~15 seconds."""
    if not ACCESS_LOG.exists():
        return

    offset = _read_offset()
    file_size = ACCESS_LOG.stat().st_size

    if file_size < offset:
        offset = 0

    if offset == file_size:
        return

    try:
        with open(ACCESS_LOG, 'r', buffering=8192) as fh:
            fh.seek(offset)
            new_lines = 0
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                client_ip = entry.get('ClientHost', '')
                request_host = entry.get('RequestHost', '')
                request_uri = entry.get('RequestURI', '')
                status = entry.get('DownstreamStatus', 0)

                if not client_ip or _is_private_ip(client_ip):
                    continue

                path = request_uri.split('?')[0] if request_uri else ''
                if path in SKIP_PATHS:
                    continue

                try:
                    if isinstance(status, int) and status >= 400:
                        continue
                except (TypeError, ValueError):
                    pass

                _upsert_traffic_row(client_ip, request_host)
                new_lines += 1

                if new_lines % 100 == 0:
                    _write_offset(fh.tell())

            _write_offset(fh.tell())

        if new_lines:
            logger.debug("Traefik log collector: processed %d new entries", new_lines)

    except Exception as exc:
        logger.warning("Traefik log collector error: %s", exc, exc_info=True)
        raise self.retry(countdown=5, exc=exc)


# ---------------------------------------------------------------------------
# Task 2: Resolve IP geolocations asynchronously
# ---------------------------------------------------------------------------
@shared_task(bind=True, ignore_result=True, max_retries=3)
def resolve_traffic_geolocations(self):
    """Batch-resolve unresolved IPs via ip-api.com. Rate-limited to 45 req/min.
    Runs every ~30 seconds, processes up to 20 IPs per batch."""
    from .models_traffic import ServiceTrafficLog

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
