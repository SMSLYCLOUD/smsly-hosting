"""Observability proxy views — bridge Django to the in-cluster Grafana/Loki/Prometheus."""
import base64
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
from decouple import config
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

logger = logging.getLogger(__name__)

GRAFANA_INTERNAL_URL = config('GRAFANA_INTERNAL_URL', default='http://grafana:3000')
GRAFANA_EXTERNAL_URL = config('GRAFANA_EXTERNAL_URL', default='https://localhost/grafana')
GRAFANA_USER = config('GRAFANA_ADMIN_USER', default='admin')
GRAFANA_PASSWORD = config('GRAFANA_PASSWORD', default='')
LOKI_INTERNAL_URL = config('LOKI_INTERNAL_URL', default='http://loki:3100')
PROMETHEUS_INTERNAL_URL = config('PROMETHEUS_INTERNAL_URL', default='http://prometheus:9090')

PROXY_TIMEOUT = 15

_LOKI_RELATIVE_RE = re.compile(r'^now(?:[-+](\d+)([smhd]))?$')


def _loki_time_to_ns(value: str) -> str:
    """Convert 'now', 'now-1h', 'now-15m' (or a raw Unix timestamp) to nanosecond string for Loki."""
    if not value:
        return value
    match = _LOKI_RELATIVE_RE.match(value)
    if match:
        amount = int(match.group(1)) if match.group(1) else 0
        unit = match.group(2) or 's'
        delta = {
            's': timedelta(seconds=amount),
            'm': timedelta(minutes=amount),
            'h': timedelta(hours=amount),
            'd': timedelta(days=amount),
        }[unit]
        ts = datetime.now(timezone.utc) - delta
        return str(int(ts.timestamp() * 1_000_000_000))
    try:
        ts = float(value)
    except ValueError:
        return value
    if ts < 1e12:
        return str(int(ts * 1_000_000_000))
    return str(int(ts))


def _grafana_auth_header() -> dict:
    if not GRAFANA_PASSWORD:
        return {}
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    return {'Authorization': f'Basic {token}'}


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def grafana_embed_url(request, dashboard_uid: str):
    """Return a signed Grafana embed URL for a given dashboard UID."""
    if not GRAFANA_PASSWORD:
        return Response(
            {'error': 'GRAFANA_PASSWORD is not configured on the backend.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    params = dict(request.query_params)
    params.setdefault('theme', 'dark')
    params.setdefault('kiosk', 'tv')

    try:
        resp = requests.get(
            f"{GRAFANA_INTERNAL_URL}/api/dashboards/uid/{dashboard_uid}",
            headers=_grafana_auth_header(),
            timeout=PROXY_TIMEOUT,
        )
        if resp.status_code == 404:
            return Response(
                {'error': f'Dashboard {dashboard_uid!r} not found in Grafana.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        resp.raise_for_status()
        dashboard = resp.json().get('dashboard', {})
    except requests.RequestException as exc:
        logger.warning("Grafana dashboard lookup failed for %s: %s", dashboard_uid, exc)
        return Response(
            {'error': 'Grafana is unreachable.', 'detail': str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    time_range = params.pop('time', ['now-1h'])[0]
    query = {
        'theme': params.pop('theme', ['dark'])[0],
        'kiosk': params.pop('kiosk', ['tv'])[0],
        'from': f'now-1h' if time_range == 'auto' else time_range,
        'to': 'now',
    }
    for key, value in params.items():
        if isinstance(value, list):
            value = value[0]
        query[key] = value

    embed_url = (
        f"{GRAFANA_EXTERNAL_URL}/d/{dashboard.get('uid', dashboard_uid)}"
        f"/{urllib.parse.quote(dashboard.get('title', dashboard_uid), safe='')}"
    )
    if query:
        embed_url = f"{embed_url}?{urllib.parse.urlencode(query)}"

    return Response({
        'url': embed_url,
        'dashboard': {
            'uid': dashboard.get('uid', dashboard_uid),
            'title': dashboard.get('title', dashboard_uid),
        },
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def loki_query(request):
    """Proxy a range query to Loki with the auth boundary at the Django layer."""
    query = request.query_params.get('query', '').strip()
    if not query:
        return Response({'error': 'query parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        limit = int(request.query_params.get('limit', '100'))
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 1000))

    payload = {
        'query': query,
        'limit': limit,
    }
    for src_key, dst_key in (
        ('start', 'start'),
        ('end', 'end'),
        ('since', 'start'),
        ('direction', 'direction'),
    ):
        value = request.query_params.get(src_key)
        if not value:
            continue
        if src_key in ('start', 'end'):
            payload[dst_key] = _loki_time_to_ns(value)
        else:
            payload[dst_key] = value

    try:
        resp = requests.get(
            f"{LOKI_INTERNAL_URL}/loki/api/v1/query_range",
            params=payload,
            timeout=PROXY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Loki query failed: %s", exc)
        return Response(
            {'error': 'Loki is unreachable.', 'detail': str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    streams = data.get('data', {}).get('result', [])
    events = []
    for stream in streams:
        labels = stream.get('stream', {})
        for ts, line in stream.get('values', []):
            events.append({
                'timestamp': ts,
                'line': line,
                'labels': labels,
            })

    return Response({
        'events': events,
        'streams': streams,
        'stats': data.get('data', {}).get('stats', {}),
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def loki_label_values(request, label: str):
    """Proxy a label-values lookup to Loki."""
    if not label:
        return Response({'error': 'label name is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        resp = requests.get(
            f"{LOKI_INTERNAL_URL}/loki/api/v1/label/{label}/values",
            params=dict(request.query_params),
            timeout=PROXY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Loki label-values lookup failed: %s", exc)
        return Response(
            {'error': 'Loki is unreachable.', 'detail': str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response({'label': label, 'values': data.get('data', [])})


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def prometheus_query(request):
    """Proxy an instant PromQL query to Prometheus."""
    query = request.query_params.get('query', '').strip()
    if not query:
        return Response({'error': 'query parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        resp = requests.get(
            f"{PROMETHEUS_INTERNAL_URL}/api/v1/query",
            params={'query': query, 'time': request.query_params.get('time')},
            timeout=PROXY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Prometheus query failed: %s", exc)
        return Response(
            {'error': 'Prometheus is unreachable.', 'detail': str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(data)
