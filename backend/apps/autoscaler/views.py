"""
Inline autoscaler views — serves status, history, config, and trigger
directly from Docker stats + Django models. No external microservice needed.
"""
import json
import logging
import os
import subprocess
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.licensing.decorators import require_tier

logger = logging.getLogger(__name__)

# ── Config defaults ──────────────────────────────────────────────────────────
DEFAULT_CHECK_INTERVAL = 60  # seconds
CACHE_KEY_STATUS = 'autoscaler:status'
CACHE_KEY_HISTORY = 'autoscaler:history'
CACHE_KEY_CONFIG = 'autoscaler:config'
CACHE_KEY_DECISIONS = 'autoscaler:decisions'
START_TIME = time.time()


# ── Docker helpers ───────────────────────────────────────────────────────────

def _docker_stats():
    """Get container stats via `docker stats --no-stream`."""
    try:
        result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format',
             '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}'],
            capture_output=True, text=True, timeout=15,
        )
        containers = {}
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) < 6:
                continue
            name = parts[0].strip()
            cpu_str = parts[1].strip().rstrip('%')
            mem_usage = parts[2].strip()
            mem_pct = parts[3].strip().rstrip('%')
            net_io = parts[4].strip()
            pids = parts[5].strip()

            # Parse memory usage: "123.4MiB / 5.678GiB"
            mem_parts = mem_usage.split('/')
            mem_used_mb = _parse_mem(mem_parts[0].strip()) if len(mem_parts) >= 1 else 0
            mem_limit_mb = _parse_mem(mem_parts[1].strip()) if len(mem_parts) >= 2 else 0

            # Parse net IO: "1.2kB / 3.4MB"
            net_parts = net_io.split('/')
            net_rx = _parse_mem(net_parts[0].strip()) if len(net_parts) >= 1 else 0
            net_tx = _parse_mem(net_parts[1].strip()) if len(net_parts) >= 2 else 0

            containers[name] = {
                'cpu_percent': _safe_float(cpu_str),
                'memory_mb': round(mem_used_mb, 1),
                'memory_limit_mb': round(mem_limit_mb, 1),
                'memory_percent': _safe_float(mem_pct),
                'net_rx_mb': round(net_rx, 2),
                'net_tx_mb': round(net_tx, 2),
                'pids': int(pids) if pids.isdigit() else 0,
            }
        return containers
    except Exception as e:
        logger.error("Failed to get docker stats: %s", e)
        return {}


def _get_system_memory():
    """Get total system memory in MB."""
    try:
        result = subprocess.run(
            ['free', '-m'], capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.split('\n'):
            if line.startswith('Mem:'):
                parts = line.split()
                return int(parts[1]) if len(parts) >= 2 else 4096
    except Exception:
        pass
    return 4096  # fallback


def _parse_mem(s):
    """Parse a memory string like '123.4MiB' or '1.5GiB' to MB."""
    s = s.strip()
    try:
        if 'GiB' in s or 'GB' in s:
            return float(s.replace('GiB', '').replace('GB', '').strip()) * 1024
        elif 'MiB' in s or 'MB' in s:
            return float(s.replace('MiB', '').replace('MB', '').strip())
        elif 'KiB' in s or 'kB' in s or 'KB' in s:
            return float(s.replace('KiB', '').replace('kB', '').replace('KB', '').strip()) / 1024
        elif 'B' in s:
            return float(s.replace('B', '').strip()) / (1024 * 1024)
        else:
            return float(s)
    except (ValueError, TypeError):
        return 0.0


def _safe_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# ── Service classification ───────────────────────────────────────────────────

INFRA_PREFIXES = (
    'smsly-hosting-db', 'smsly-hosting-redis', 'smsly-hosting-traefik',
    'smsly-hosting-nginx', 'smsly-hosting-pgbouncer', 'smsly-hosting-registry',
    'smsly-hosting-socket-proxy', 'smsly-hosting-route-fallback',
)


def _classify_container(name):
    """Classify a container as infra vs app and determine type."""
    if any(name.startswith(p) for p in INFRA_PREFIXES):
        return None  # Skip infra containers
    if 'celery-beat' in name:
        return 'celery', 'platform'
    if 'celery' in name:
        return 'celery', 'platform'
    if 'backend' in name:
        return 'gunicorn', 'platform'
    if 'frontend' in name and 'smsly-hosting' in name:
        return 'gunicorn', 'platform'
    # Customer apps
    return 'gunicorn', name.split('-')[0] if '-' in name else name


def _build_services_map(stats):
    """Build the services map from docker stats, matching frontend interface."""
    services = {}
    config = _get_config()

    for name, s in stats.items():
        classification = _classify_container(name)
        if classification is None:
            continue

        svc_type, app = classification
        svc_config = config.get('services', {}).get(name, {})

        services[name] = {
            'type': svc_type,
            'app': app,
            'priority': svc_config.get('priority', 5),
            'status': 'running',
            'demand_score': min(s['cpu_percent'] + s['memory_percent'], 100),
            'cpu_percent': s['cpu_percent'],
            'memory_mb': s['memory_mb'],
            'memory_limit_mb': s['memory_limit_mb'],
            'memory_percent': s['memory_percent'],
            'net_rx_mb': s['net_rx_mb'],
            'net_tx_mb': s['net_tx_mb'],
            'pids': s['pids'],
            'current_workers': 1,
            'min_workers': svc_config.get('min_workers', 1),
            'max_workers': svc_config.get('max_workers', 4),
            'last_action': 'none',
            'last_action_at': timezone.now().isoformat(),
        }
    return services


# ── Config ───────────────────────────────────────────────────────────────────

def _get_config():
    """Get autoscaler config from cache."""
    config = cache.get(CACHE_KEY_CONFIG)
    if config is None:
        config = {
            'total_system_mb': _get_system_memory(),
            'infra_reserve_mb': 512,
            'check_interval': DEFAULT_CHECK_INTERVAL,
            'services': {},
        }
        cache.set(CACHE_KEY_CONFIG, config, timeout=None)
    return config


# ── History tracking ─────────────────────────────────────────────────────────

def _record_history(services, total_mem, infra_reserve):
    """Append current stats to history in cache."""
    history = cache.get(CACHE_KEY_HISTORY) or {
        'timestamps': [], 'services': {}, 'budget': {'used_mb': [], 'free_mb': []}
    }

    now = timezone.now().isoformat()
    history['timestamps'].append(now)

    total_used = 0
    for name, svc in services.items():
        if name not in history['services']:
            history['services'][name] = {
                'cpu': [], 'memory_mb': [], 'demand_score': [], 'workers': []
            }
        h = history['services'][name]
        h['cpu'].append(svc['cpu_percent'])
        h['memory_mb'].append(svc['memory_mb'])
        h['demand_score'].append(svc['demand_score'])
        h['workers'].append(svc['current_workers'])
        total_used += svc['memory_mb']

    app_budget = total_mem - infra_reserve
    history['budget']['used_mb'].append(round(total_used, 1))
    history['budget']['free_mb'].append(round(max(app_budget - total_used, 0), 1))

    # Keep only last 120 data points (~2 hours at 1/min)
    max_points = 120
    if len(history['timestamps']) > max_points:
        history['timestamps'] = history['timestamps'][-max_points:]
        history['budget']['used_mb'] = history['budget']['used_mb'][-max_points:]
        history['budget']['free_mb'] = history['budget']['free_mb'][-max_points:]
        for h in history['services'].values():
            for key in ('cpu', 'memory_mb', 'demand_score', 'workers'):
                h[key] = h[key][-max_points:]

    cache.set(CACHE_KEY_HISTORY, history, timeout=7200)
    return history


# ── Core check ───────────────────────────────────────────────────────────────

def _run_autoscaler_check():
    """Run the autoscaler check and return status."""
    config = _get_config()
    stats = _docker_stats()
    services = _build_services_map(stats)
    total_mem = config.get('total_system_mb', _get_system_memory())
    infra_reserve = config.get('infra_reserve_mb', 512)

    _record_history(services, total_mem, infra_reserve)

    total_used = sum(s['memory_mb'] for s in services.values())
    app_budget = total_mem - infra_reserve

    status_data = {
        'status': 'active',
        'uptime_seconds': round(time.time() - START_TIME),
        'check_interval': config.get('check_interval', DEFAULT_CHECK_INTERVAL),
        'last_check_at': timezone.now().isoformat(),
        'budget': {
            'total_system_mb': total_mem,
            'infra_reserve_mb': infra_reserve,
            'app_budget_mb': app_budget,
            'used_mb': round(total_used, 1),
            'free_mb': round(max(app_budget - total_used, 0), 1),
        },
        'services': services,
        'recent_decisions': cache.get(CACHE_KEY_DECISIONS, []),
    }

    cache.set(CACHE_KEY_STATUS, status_data, timeout=300)
    return status_data


# ── Views ────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAdminUser])
@require_tier('pro', 'enterprise')
def autoscaler_status(request):
    """Return current autoscaler status with live container stats."""
    try:
        status_data = _run_autoscaler_check()
        return Response(status_data)
    except Exception as e:
        logger.error("Autoscaler status error: %s", e)
        return Response(
            {'error': str(e), 'autoscaler_reachable': False},
            status=503,
        )


@api_view(['GET'])
@permission_classes([IsAdminUser])
@require_tier('pro', 'enterprise')
def autoscaler_history(request):
    """Return autoscaler history from cache."""
    try:
        history = cache.get(CACHE_KEY_HISTORY)
        if not history:
            # Run a check to populate initial data
            _run_autoscaler_check()
            history = cache.get(CACHE_KEY_HISTORY, {
                'timestamps': [], 'services': {},
                'budget': {'used_mb': [], 'free_mb': []},
            })
        return Response(history)
    except Exception as e:
        logger.error("Autoscaler history error: %s", e)
        return Response({'error': str(e)}, status=503)


@api_view(['POST'])
@permission_classes([IsAdminUser])
@require_tier('pro', 'enterprise')
def autoscaler_config(request):
    """Update autoscaler config."""
    try:
        config = _get_config()
        config.update(request.data)
        cache.set(CACHE_KEY_CONFIG, config, timeout=None)
        return Response({'status': 'updated', 'config': config})
    except Exception as e:
        logger.error("Autoscaler config error: %s", e)
        return Response({'error': str(e)}, status=503)


@api_view(['POST'])
@permission_classes([IsAdminUser])
@require_tier('pro', 'enterprise')
def autoscaler_trigger(request):
    """Trigger an immediate autoscaler check."""
    try:
        status_data = _run_autoscaler_check()
        return Response(status_data)
    except Exception as e:
        logger.error("Autoscaler trigger error: %s", e)
        return Response({'error': str(e)}, status=503)
