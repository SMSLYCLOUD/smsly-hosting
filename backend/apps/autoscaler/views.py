"""
Inline autoscaler views — serves status, history, config, trigger,
scale-out/in decisions, and health checks.
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

from apps.autoscaler import registry
from apps.autoscaler.models import AutoscalerConfig


logger = logging.getLogger(__name__)

# ── Config constants ────────────────────────────────────────────────────────
DEFAULT_CHECK_INTERVAL = 60  # seconds
CACHE_KEY_STATUS = "autoscaler:status"
CACHE_KEY_HISTORY = "autoscaler:history"
CACHE_KEY_CONFIG = "autoscaler:config"
CACHE_KEY_DECISIONS = "autoscaler:decisions"
CACHE_KEY_LAST_SCALE = "autoscaler:last_scale"  # dict {service_name: timestamp}
COOLDOWN_UP = 60      # seconds (1 min)
COOLDOWN_DOWN = 300   # seconds (5 min)
START_TIME = time.time()


# ── Docker helpers ───────────────────────────────────────────────────────────
def _docker_stats():
    """Collect container metrics via `docker stats --no-stream`."""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.PIDs}}"],
            capture_output=True, text=True, timeout=15,
        )
        containers = {}
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            name, cpu_str, mem_usage, mem_pct, net_io, pids = parts
            mem_parts = mem_usage.split("/")
            mem_used_mb = _parse_mem(mem_parts[0].strip()) if len(mem_parts) >= 1 else 0
            mem_limit_mb = _parse_mem(mem_parts[1].strip()) if len(mem_parts) >= 2 else 0
            net_parts = net_io.split("/")
            net_rx = _parse_mem(net_parts[0].strip()) if len(net_parts) >= 1 else 0
            net_tx = _parse_mem(net_parts[1].strip()) if len(net_parts) >= 2 else 0

            containers[name] = {
                "cpu_percent": _safe_float(cpu_str.replace('%', '')),
                "memory_mb": round(mem_used_mb, 1),
                "memory_limit_mb": round(mem_limit_mb, 1),
                "memory_percent": _safe_float(mem_pct.replace('%', '')),
                "net_rx_mb": round(net_rx, 2),
                "net_tx_mb": round(net_tx, 2),
                "pids": int(pids) if pids.isdigit() else 0,
            }
        return containers
    except Exception as exc:
        logger.error("Failed to get docker stats: %s", exc)
        return {}


def _parse_mem(s: str) -> float:
    """Convert strings like '123.4MiB' or '1.5GiB' to megabytes (float)."""
    s = s.strip()
    try:
        if "GiB" in s or "GB" in s:
            return float(s.replace("GiB", "").replace("GB", "").strip()) * 1024
        if "MiB" in s or "MB" in s:
            return float(s.replace("MiB", "").replace("MB", "").strip())
        if "KiB" in s or "kB" in s or "KB" in s:
            return float(s.replace("KiB", "").replace("kB", "").replace("KB", "").strip()) / 1024
        if "B" in s:
            return float(s.replace("B", "").strip()) / (1024 * 1024)
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _safe_float(v: str) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


# ── System memory detection (prefer psutil) ─────────────────────────────────
def _get_system_memory() -> int:
    """Return total RAM in MB, falling back to `free -m` if psutil unavailable."""
    try:
        import psutil
        return int(psutil.virtual_memory().total / (1024 * 1024))
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["free", "-m"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                return int(parts[1]) if len(parts) >= 2 else 4096
    except Exception:
        pass
    return 4096  # safe fallback


# ── Service classification (now delegated to the registry) ─────────────────
def _classify_container(name: str):
    """
    Wrapper that uses the extensible registry.
    Returns (svc_type, app_name) or None for infrastructure containers.
    """
    return registry.classify(name)


# ── Build the services map used for decisions ─────────────────────────────────
def _build_services_map(stats: dict) -> dict:
    """Create the internal service representation from raw Docker stats."""
    services = {}
    config = _get_config()

    for name, s in stats.items():
        classification = _classify_container(name)
        if classification is None:
            continue  # skip infra containers

        svc_type, app = classification
        svc_cfg = config.get("services", {}).get(name, {})
        services[name] = {
            "type": svc_type,
            "app": app,
            "priority": svc_cfg.get("priority", 5),
            "status": "running",
            "demand_score": min(s["cpu_percent"] + s["memory_percent"], 100),
            "cpu_percent": s["cpu_percent"],
            "memory_mb": round(s["memory_mb"], 1),
            "memory_limit_mb": round(s["memory_limit_mb"], 1),
            "memory_percent": s["memory_percent"],
            "net_rx_mb": round(s["net_rx_mb"], 2),
            "net_tx_mb": round(s["net_tx_mb"], 2),
            "pids": s["pids"],
            "current_workers": 1, # TODO: Detect real replica count from SDK
            "min_workers": svc_cfg.get("min_workers", 1),
            "max_workers": svc_cfg.get("max_workers", 4),
            "last_action": "none",
            "last_action_at": timezone.now().isoformat(),
        }
    return services


# ── Configuration handling (persisted in DB) ─────────────────────────────────
def _get_config():
    """
    Retrieve the autoscaler configuration from the persistent store.
    The first call creates a default row with system-aware values.
    """
    config = cache.get(CACHE_KEY_CONFIG)
    if config is None:
        config = AutoscalerConfig.get_config()
        if not config:
            config = {
                'total_system_mb': _get_system_memory(),
                'infra_reserve_mb': 512,
                'check_interval': DEFAULT_CHECK_INTERVAL,
                'services': {},
            }
            AutoscalerConfig.save_config(config)
        cache.set(CACHE_KEY_CONFIG, config, timeout=3600)
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


# ── Decision engine – when to scale up / down ─────────────────────────────────
def _decide_scaling(services: dict) -> list[dict]:
    """
    Simple policy:
      * demand_score > 70%  -> scale up (if current_workers < max_workers)
      * demand_score < 30%  -> scale down (if current_workers > min_workers)
    Respects cooldowns to avoid flapping.
    """
    actions = []
    now_ts = time.time()
    last_scale = cache.get(CACHE_KEY_LAST_SCALE, {})
    now_iso = timezone.now().isoformat()

    for name, svc in services.items():
        cur = svc["current_workers"]
        min_w = svc["min_workers"]
        max_w = svc["max_workers"]
        demand = svc["demand_score"]

        # Determine whether we are allowed to act based on cooldown
        last = last_scale.get(name, 0)
        action_taken = None
        
        if demand > 70 and cur < max_w:
            if (now_ts - last) >= COOLDOWN_UP:
                action_taken = {
                    "ts": now_iso,
                    "name": name,
                    "action": "scale_up",
                    "target_workers": min(cur + 1, max_w),
                    "reason": f"high demand ({demand:.1f}%)",
                }
        elif demand < 30 and cur > min_w:
            if (now_ts - last) >= COOLDOWN_DOWN:
                action_taken = {
                    "ts": now_iso,
                    "name": name,
                    "action": "scale_down",
                    "target_workers": max(cur - 1, min_w),
                    "reason": f"low demand ({demand:.1f}%)",
                }

        if action_taken:
            actions.append(action_taken)
            last_scale[name] = now_ts

    if actions:
        # Store recent decisions (keep last 200)
        recent = cache.get(CACHE_KEY_DECISIONS, [])
        cache.set(CACHE_KEY_DECISIONS, (actions + recent)[:200], timeout=None)
        cache.set(CACHE_KEY_LAST_SCALE, last_scale, timeout=None)

    return actions


# ── Apply scaling via Docker SDK ─────────────────────────────────────────────
def _apply_scaling(decision: dict):
    """
    Execute a scale_up or scale_down using the Docker SDK.
    """
    name = decision["name"]
    target = decision["target_workers"]
    action = decision["action"]

    try:
        import docker
        client = docker.from_env()
        
        # Best effort: Try Swarm Service first, then look for container-based scaling
        try:
            service = client.services.get(name)
            service.scale(target)
            logger.info("Autoscaler: Scaled Swarm service %s to %d", name, target)
        except Exception:
            # If not a swarm service, maybe it's a standalone container?
            # Standalone containers don't have 'scale' in the same way.
            # We would usually need docker-compose or similar to orchestrate multiple replicas.
            # For now, we log the intent.
            logger.warning("Autoscaler: Scaling for non-swarm container %s requested, but not fully implemented.", name)
            
        logger.info(
            "Autoscaler: %s %s -> %s (reason: %s)",
            name,
            action,
            target,
            decision.get("reason", "no reason"),
        )
    except Exception as exc:
        logger.error("Autoscaler scaling failed for %s: %s", name, exc)


# ── Core health-check routine ─────────────────────────────────────────────────
def _run_autoscaler_check():
    """Run the full autoscaler cycle: metrics -> history -> decisions -> apply."""
    config = _get_config()
    stats = _docker_stats()
    services = _build_services_map(stats)
    
    total_mem = config.get('total_system_mb', _get_system_memory())
    infra_reserve = config.get('infra_reserve_mb', 512)

    # Restore legacy history recording
    _record_history(services, total_mem, infra_reserve)
    
    # NEW: Decide and apply scaling
    decisions = _decide_scaling(services)
    for action in decisions:
        _apply_scaling(action)

    total_used = sum(s["memory_mb"] for s in services.values())
    app_budget = total_mem - infra_reserve

    status_data = {
        "status": "active",
        "uptime_seconds": round(time.time() - START_TIME),
        "check_interval": config.get('check_interval', DEFAULT_CHECK_INTERVAL),
        "last_check_at": timezone.now().isoformat(),
        "budget": {
            "total_system_mb": total_mem,
            "infra_reserve_mb": infra_reserve,
            "app_budget_mb": app_budget,
            "used_mb": round(total_used, 1),
            "free_mb": round(max(app_budget - total_used, 0), 1),
        },
        "services": services,
        "recent_decisions": cache.get(CACHE_KEY_DECISIONS, []),
    }

    # Cache the status for quick retrieval
    cache.set(CACHE_KEY_STATUS, status_data, timeout=300)
    return status_data


# ── DRF endpoints ───────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAdminUser])
def autoscaler_status(request):
    """Return the current autoscaler status (cached for 5 min)."""
    try:
        return Response(_run_autoscaler_check())
    except Exception as exc:
        logger.error("Autoscaler status error: %s", exc)
        return Response({"error": str(exc)}, status=503)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def autoscaler_history(request):
    """Return historical metrics stored in cache."""
    try:
        history = cache.get(CACHE_KEY_HISTORY)
        if not history:
            # Prime the history cache by forcing a check
            _run_autoscaler_check()
            history = cache.get(CACHE_KEY_HISTORY, {"timestamps": [], "services": {}, "budget": {"used_mb": [], "free_mb": []}})
        return Response(history)
    except Exception as exc:
        logger.error("Autoscaler history error: %s", exc)
        return Response({"error": str(exc)}, status=503)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autoscaler_config(request):
    """Update autoscaler configuration (persisted in DB)."""
    try:
        cfg = _get_config()
        cfg.update(request.data)
        AutoscalerConfig.save_config(cfg)
        cache.set(CACHE_KEY_CONFIG, cfg, timeout=None)
        return Response({"status": "updated", "config": cfg})
    except Exception as exc:
        logger.error("Autoscaler config error: %s", exc)
        return Response({"error": str(exc)}, status=503)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autoscaler_trigger(request):
    """Manually trigger an immediate autoscaler check."""
    try:
        return Response(_run_autoscaler_check())
    except Exception as exc:
        logger.error("Autoscaler trigger error: %s", exc)
        return Response({"error": str(exc)}, status=503)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autoscaler_scale(request):
    """
    Manual endpoint to force a scaling round.
    """
    try:
        return Response(_run_autoscaler_check())
    except Exception as exc:
        logger.error("Autoscaler scale endpoint error: %s", exc)
        return Response({"error": str(exc)}, status=503)
