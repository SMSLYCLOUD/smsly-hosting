"""Observability proxy views — bridge Django to the in-cluster Grafana/Loki/Prometheus."""
import base64
import logging
import re
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta

import requests
from decouple import config
from django.conf import settings
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

logger = logging.getLogger(__name__)

GRAFANA_INTERNAL_URL = config('GRAFANA_INTERNAL_URL', default='http://smsly-grafana:3000')
GRAFANA_USER = config('GRAFANA_ADMIN_USER', default='admin')
GRAFANA_PASSWORD = config('GRAFANA_PASSWORD', default='')
LOKI_INTERNAL_URL = config('LOKI_INTERNAL_URL', default='http://smsly-loki:3100')
PROMETHEUS_INTERNAL_URL = config('PROMETHEUS_INTERNAL_URL', default='http://smsly-prometheus:9090')

PROXY_TIMEOUT = 15

# SECURITY: cap query length and restrict to a safe character set so a user
# cannot smuggle PromQL/LogQL tokens that reach other tenants or amplify an
# SSRF on the in-cluster observability backends.
MAX_PROMETHEUS_QUERY_LENGTH = 4096
MAX_LOKI_QUERY_LENGTH = 4096
# Allow valid LogQL / PromQL characters. Includes regex operators (~, |, +, *, ?,
# ^, $, \), log-range syntax (> <), and backtick for LogQL label matchers.
SAFE_QUERY_CHARS_RE = re.compile(
    r"^[a-zA-Z0-9_=.{}, !\"'\(\)\[\]:\-~|+*?^$\\><`]+$"
)
ALLOWED_LOKI_LABELS = frozenset({
    'service', 'job', 'level', 'status', 'method', 'route',
})
MAX_PROMETHEUS_TIME_RANGE = timedelta(days=30)

_LOKI_RELATIVE_RE = re.compile(r'^now(?:[-+](\d+)([smhd]))?$')


class ObservabilityRateThrottle(UserRateThrottle):
    """30 req/min/user. Defined inline so it does not depend on
    settings.DEFAULT_THROTTLE_RATES having a matching entry.
    """
    scope = 'observability'
    rate = '30/minute'


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
        ts_dt = datetime.now(UTC) - delta
        return str(int(ts_dt.timestamp() * 1_000_000_000))
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


def _validate_query_chars(query: str) -> str:
    """Reject queries whose length or character set is unsafe."""
    if not query:
        return 'Query is required.'
    if not SAFE_QUERY_CHARS_RE.match(query):
        return 'Query contains characters outside the allowed PromQL/LogQL set.'
    return ''


def _user_owned_service_names(user) -> list[str]:
    """Return the names of services the user owns, used for tenant scoping."""
    from apps.deployments.models import Service
    names = list(
        Service.objects
        .filter(owner=user)
        .values_list('compose_main_service', 'name')
    )
    out: list[str] = []
    for compose_name, name in names:
        candidate = (compose_name or '').strip() or (name or '').strip()
        if candidate:
            out.append(candidate)
    return out


def _escape_logql_regex_literal(name: str) -> str:
    """Escape RE2 regex meta-characters in *name* so it can be used as a
    literal inside a ``=~"..."`` matcher.

    Unlike ``re.escape()`` this does NOT escape ``-`` (which is only special
    inside ``[]`` in RE2) because LogQL/RE2 rejects over-escaped hyphens as
    invalid regex.
    """
    _logql_meta = re.compile(r'([\\|.+*?^${}()\[\]])')
    return _logql_meta.sub(r'\\\1', name)


def _scope_query_to_tenant(query: str, service_names: list[str]) -> str:
    """Inject a ``compose_service=…"<names>"`` filter so the query can only
    match the user's own services.

    Uses ``=`` (exact) for a single service and ``=~`` (regex) with ``|`` for
    multiple services.

    If the query already has a ``{...}`` selector with a ``compose_service``
    matcher, the existing matcher is REPLACED with the tenant filter rather
    than appended. Two matchers for the same label key in one stream selector
    is invalid LogQL and causes Loki to return 400 Bad Request.
    """
    names = [n for n in service_names if n]
    if not names:
        raise ValueError("User has no services to scope the query to.")

    if len(names) == 1:
        tenant_filter = f'compose_service="{names[0]}"'
    else:
        escaped = [_escape_logql_regex_literal(n) for n in names]
        tenant_filter = f'compose_service=~"{"|".join(escaped)}"'

    if '{' in query:
        idx = query.index('{')
        end_idx = query.index('}', idx)
        inner = query[idx + 1:end_idx].strip()
        # Remove any existing compose_service matcher to avoid duplicate label keys
        cleaned = re.sub(
            r'compose_service\s*(?:=|!=|=~|!~)\s*"[^"]*"\s*,?\s*',
            '',
            inner,
        ).strip().strip(',').strip()
        new_inner = f'{cleaned}, {tenant_filter}' if cleaned else tenant_filter
        return query[:idx + 1] + new_inner + query[end_idx:]
    return '{' + tenant_filter + '}'


def _parse_prometheus_time(raw: str | None) -> str | None:
    """Restrict the ``time`` parameter to [now-30d, now]."""
    if raw is None or raw == '':
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    now = datetime.now(UTC).timestamp()
    lower = (datetime.now(UTC) - MAX_PROMETHEUS_TIME_RANGE).timestamp()
    if ts < lower or ts > now:
        return None
    return raw


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def grafana_embed_url(request, dashboard_uid: str):
    """Return a signed Grafana embed URL for a given dashboard UID."""
    if not GRAFANA_PASSWORD:
        return Response(
            {'error': 'GRAFANA_PASSWORD is not configured on the backend.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    params = dict(request.GET)
    params.setdefault('theme', 'dark')
    params.setdefault('kiosk', 'tv')
    # Resolve var-service UUID to service name for Grafana template variable.
    # Tenant-scoped: if the caller doesn't own the service, resolved becomes ""
    # which hides all metrics in the dashboard instead of leaking another
    # tenant's data.
    var_service = params.get('var-service', '')
    if var_service:
        resolved = _resolve_service_var(var_service, user=request.user)
        params['var-service'] = resolved

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
    except requests.RequestException:
        logger.exception("Grafana dashboard lookup failed for %s", dashboard_uid)
        return Response(
            {'error': 'Grafana is unreachable.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    time_range = params.pop('time', ['now-1h'])[0]
    query = {
        'theme': params.pop('theme', ['dark'])[0],
        'kiosk': params.pop('kiosk', ['tv'])[0],
        'from': 'now-1h' if time_range == 'auto' else time_range,
        'to': 'now',
    }
    for key, value in params.items():
        if isinstance(value, list):
            value = value[0]
        query[key] = value

    grafana_external = getattr(settings, 'GRAFANA_EXTERNAL_URL', None)
    if not grafana_external:
        site_url = getattr(settings, 'SITE_URL', '')
        if site_url:
            grafana_external = f"{site_url.rstrip('/')}/grafana"
        else:
            grafana_external = 'https://localhost/grafana'

    embed_url = (
        f"{grafana_external}/d/{dashboard.get('uid', dashboard_uid)}"
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


def _resolve_service_var(var_service: str, user=None) -> str:
    """Resolve a var-service parameter (UUID or name) to a compose service name.

    When *user* is provided, the service lookup is scoped to services the
    user owns or has team access to.  A non-owned service returns an empty
    string so Grafana cannot leak another tenant's metrics.
    """
    from apps.deployments.models import Service
    from django.db.models import Q
    try:
        uuid.UUID(var_service)
        qs = Service.objects.filter(id=var_service)
    except (ValueError, Exception):
        qs = Service.objects.filter(name=var_service)
    if user:
        qs = qs.filter(
            Q(owner=user) | Q(project__team__members__user=user)
        )
    svc = qs.first()
    if svc:
        # For Grafana dashboards, use service.name (matches docker-labels-exporter
        # service_name label). compose_main_service (e.g. "web") does not match.
        return svc.name
    if user:
        return ""
    return var_service


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([ObservabilityRateThrottle])
def loki_query(request):
    """Proxy a range query to Loki with the auth boundary at the Django layer."""
    query = request.GET.get('query', '').strip()
    if len(query) > MAX_LOKI_QUERY_LENGTH:
        return Response(
            {'error': f'Query exceeds {MAX_LOKI_QUERY_LENGTH} characters.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    char_error = _validate_query_chars(query)
    if char_error:
        return Response({'error': char_error}, status=status.HTTP_400_BAD_REQUEST)

    try:
        service_names = _user_owned_service_names(request.user)
    except Exception as exc:
        logger.warning("Loki query service lookup failed: %s", exc)
        return Response(
            {'error': 'Unable to resolve user services for tenant scoping.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        query = _scope_query_to_tenant(query, service_names)
    except ValueError:
        logger.exception("Tenant query scoping failed")
        return Response({'error': 'An observability query failed.'}, status=status.HTTP_400_BAD_REQUEST)

    # Resolve UUID in compose_service filter (safety net for unresolved UUIDs)
    uuid_match = re.search(
        r'compose_service(=~|!=|!~|=)"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"',
        query,
    )
    if uuid_match:
        from apps.deployments.models import Service
        op = uuid_match.group(1)
        service_id = uuid_match.group(2)
        svc = Service.objects.filter(id=service_id).first()
        if svc:
            normalized_name = svc.name.lower().replace(' ', '-')
            if svc.deploy_mode == 'COMPOSE':
                replacement = f'compose_project{op}"{normalized_name}"'
            else:
                replacement = f'compose_service{op}"{svc.name}"'
            query = query.replace(uuid_match.group(0), replacement)

    try:
        limit = int(request.GET.get('limit', '100'))
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
        value = request.GET.get(src_key)
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
    except requests.RequestException:
        logger.exception("Loki query failed")
        return Response(
            {'error': 'Loki is unreachable.'},
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
@throttle_classes([ObservabilityRateThrottle])
def loki_label_values(request, label: str):
    """Proxy a label-values lookup to Loki."""
    if not label:
        return Response({'error': 'label name is required'}, status=status.HTTP_400_BAD_REQUEST)
    if label not in ALLOWED_LOKI_LABELS:
        return Response(
            {
                'error': (
                    f"label {label!r} is not in the allowed set: "
                    f"{sorted(ALLOWED_LOKI_LABELS)}"
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        resp = requests.get(
            f"{LOKI_INTERNAL_URL}/loki/api/v1/label/{label}/values",
            params=dict(request.GET),
            timeout=PROXY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        logger.exception("Loki label-values lookup failed")
        return Response(
            {'error': 'Loki is unreachable.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response({'label': label, 'values': data.get('data', [])})


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([ObservabilityRateThrottle])
def prometheus_query(request):
    """Proxy an instant PromQL query to Prometheus."""
    query = request.GET.get('query', '').strip()
    if len(query) > MAX_PROMETHEUS_QUERY_LENGTH:
        return Response(
            {'error': f'Query exceeds {MAX_PROMETHEUS_QUERY_LENGTH} characters.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    char_error = _validate_query_chars(query)
    if char_error:
        return Response({'error': char_error}, status=status.HTTP_400_BAD_REQUEST)

    try:
        service_names = _user_owned_service_names(request.user)
    except Exception as exc:
        logger.warning("Prometheus query service lookup failed: %s", exc)
        return Response(
            {'error': 'Unable to resolve user services for tenant scoping.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        query = _scope_query_to_tenant(query, service_names)
    except ValueError:
        logger.exception("Tenant query scoping failed for Prometheus")
        return Response({'error': 'An observability query failed.'}, status=status.HTTP_400_BAD_REQUEST)

    raw_time = request.GET.get('time')
    if raw_time:
        parsed_time = _parse_prometheus_time(raw_time)
        if parsed_time is None:
            return Response(
                {'error': 'time must be a Unix timestamp within the last 30 days.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        parsed_time = None

    try:
        params = {'query': query}
        if parsed_time is not None:
            params['time'] = parsed_time
        resp = requests.get(
            f"{PROMETHEUS_INTERNAL_URL}/api/v1/query",
            params=params,
            timeout=PROXY_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        logger.exception("Prometheus query failed")
        return Response(
            {'error': 'Prometheus is unreachable.'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(data)
