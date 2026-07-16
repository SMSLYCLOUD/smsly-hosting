"""
Inline autoscaler views — serves status, history, config, trigger,
scale-out/in decisions, and health checks for the *container-level*
autoscaler dashboard.

This is the platform-level autoscaler that scales the *platform's own*
containers (celery workers, gunicorn instances, customer apps via
Swarm/K8s deployments) — distinct from the per-``Service`` autoscaler
in ``apps.deployments.services.autoscaler`` and
``apps.deployments.tasks_autoscale``.

The container metrics collection, K8s / Docker stats parsing, and
unit-conversion helpers now live in ``apps.autoscaler.engine.container_metrics``
so they are shared with the per-``Service`` pipeline. The container-
level decision policy (demand_score on raw container metrics) and the
REST API surface are preserved as-is for backward compatibility.
"""
import logging
import subprocess
import time

from django.core.cache import cache
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.autoscaler import registry
from apps.autoscaler.engine.container_metrics import (
    collect_container_stats,
    init_k8s,
    k8s_available,
    k8s_client,
)
from apps.autoscaler.models import AutoscalerConfig

logger = logging.getLogger(__name__)

# ── Config constants ────────────────────────────────────────────────────────
DEFAULT_CHECK_INTERVAL = 60  # seconds
CACHE_KEY_STATUS = "autoscaler:status"
CACHE_KEY_HISTORY = "autoscaler:history"
CACHE_KEY_CONFIG = "autoscaler:config"
CACHE_KEY_DECISIONS = "autoscaler:decisions"
CACHE_KEY_LAST_SCALE = "autoscaler:last_scale"
COOLDOWN_UP = 60
COOLDOWN_DOWN = 300
START_TIME = time.time()


# Initialise K8s once on import so the K8S_AVAILABLE flag the rest of
# the code expects is set. Existing call sites that check the module
# attribute continue to work.
init_k8s()


# ── System memory detection (prefer psutil) ─────────────────────────────────
def _get_system_memory() -> int:
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
    return 4096


# ── Service classification (now delegated to the registry) ─────────────────
def _classify_container(name: str):
    return registry.classify(name)


# ── Build the services map used for decisions ───────────────────────────────
def _build_services_map(stats: dict) -> dict:
    services = {}
    config = _get_config()
    for name, s in stats.items():
        classification = _classify_container(name)
        if classification is None:
            continue
        svc_type, app = classification
        svc_cfg = config.get("services", {}).get(name, {})
        services[name] = {
            "type": svc_type,
            "app": app,
            "priority": svc_cfg.get("priority", 5),
            "status": "running",
            "demand_score": min(s["cpu_percent"] + s["memory_percent"], 100) / 100.0,
            "cpu_percent": s["cpu_percent"],
            "memory_mb": round(s["memory_mb"], 1),
            "memory_limit_mb": round(s["memory_limit_mb"], 1),
            "memory_percent": s["memory_percent"],
            "net_rx_mb": round(s["net_rx_mb"], 2),
            "net_tx_mb": round(s["net_tx_mb"], 2),
            "pids": s["pids"],
            "current_workers": 1,
            "min_workers": svc_cfg.get("min_workers", 1),
            "max_workers": svc_cfg.get("max_workers", 4),
            "last_action": "none",
            "last_action_at": timezone.now().isoformat(),
        }
    return services


# ── Configuration handling (persisted in DB) ───────────────────────────────
def _get_config():
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


# ── History tracking ───────────────────────────────────────────────────────
def _record_history(services, total_mem, infra_reserve):
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


# ── Decision engine – when to scale up / down ───────────────────────────────
def _decide_scaling(services: dict) -> list[dict]:
    actions = []
    now_ts = time.time()
    last_scale = cache.get(CACHE_KEY_LAST_SCALE, {})
    now_iso = timezone.now().isoformat()
    for name, svc in services.items():
        cur = svc["current_workers"]
        min_w = svc["min_workers"]
        max_w = svc["max_workers"]
        demand = svc["demand_score"]
        last = last_scale.get(name, 0)
        action_taken = None
        if demand > 0.70 and cur < max_w:
            if (now_ts - last) >= COOLDOWN_UP:
                target_w = min(cur + 1, max_w)
                action_taken = {
                    "timestamp": now_iso,
                    "container": name,
                    "action": "scale_up",
                    "current_workers": cur,
                    "target_workers": target_w,
                    "current_memory_mb": svc["memory_mb"],
                    "target_memory_mb": (svc["memory_mb"] / cur) * target_w if cur > 0 else svc["memory_mb"] * 2,
                    "reason": f"high demand ({demand * 100:.1f}%)",
                }
        elif demand < 0.30 and cur > min_w:
            if (now_ts - last) >= COOLDOWN_DOWN:
                target_w = max(cur - 1, min_w)
                action_taken = {
                    "timestamp": now_iso,
                    "container": name,
                    "action": "scale_down",
                    "current_workers": cur,
                    "target_workers": target_w,
                    "current_memory_mb": svc["memory_mb"],
                    "target_memory_mb": (svc["memory_mb"] / cur) * target_w if cur > 0 else svc["memory_mb"] / 2,
                    "reason": f"low demand ({demand * 100:.1f}%)",
                }
        if action_taken:
            actions.append(action_taken)
            last_scale[name] = now_ts
    if actions:
        recent = cache.get(CACHE_KEY_DECISIONS, [])
        cache.set(CACHE_KEY_DECISIONS, (actions + recent)[:200], timeout=None)
        cache.set(CACHE_KEY_LAST_SCALE, last_scale, timeout=None)
    return actions


# ── Apply scaling via Docker SDK or K8s API ────────────────────────────────
def _apply_scaling(decision: dict):
    name = decision["container"]
    target = decision["target_workers"]
    action = decision["action"]
    if k8s_available():
        try:
            apps_v1 = k8s_client.AppsV1Api()
            autoscaling_v1 = k8s_client.AutoscalingV1Api()
            namespace = "default"
            # Skip if HPA exists for this deployment — HPA owns
            # replica count reconciliation; direct patching would
            # fight the HPA control loop.
            try:
                autoscaling_v1.read_namespaced_horizontal_pod_autoscaler(name, namespace)
                logger.info(
                    "Autoscaler: HPA exists for %s/%s — delegating to HPA",
                    namespace, name,
                )
                return
            except Exception:
                pass  # no HPA — proceed with direct scaling
            deployment = apps_v1.read_namespaced_deployment(name, namespace)
            deployment.spec.replicas = target
            apps_v1.patch_namespaced_deployment(name, namespace, deployment)
            logger.info("Autoscaler: Scaled K8s deployment %s/%s to %d", namespace, name, target)
        except Exception as exc:
            logger.error("Autoscaler K8s scaling failed for %s: %s", name, exc)
        return
    try:
        import docker
        client = docker.from_env()
        try:
            service = client.services.get(name)
            service.scale(target)
            logger.info("Autoscaler: Scaled Swarm service %s to %d", name, target)
        except Exception:
            logger.debug("Autoscaler: Scaling for non-swarm container %s requested, but not fully implemented.", name)
        logger.info(
            "Autoscaler: %s %s -> %s (reason: %s)",
            name, action, target, decision.get("reason", "no reason"),
        )
    except Exception as exc:
        logger.error("Autoscaler scaling failed for %s: %s", name, exc)


# ── Core health-check routine ──────────────────────────────────────────────
def _run_autoscaler_check():
    config = _get_config()
    stats = collect_container_stats()
    services = _build_services_map(stats)
    total_mem = config.get('total_system_mb', _get_system_memory())
    infra_reserve = config.get('infra_reserve_mb', 512)
    _record_history(services, total_mem, infra_reserve)
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
    cache.set(CACHE_KEY_STATUS, status_data, timeout=300)
    return status_data


# ── DRF endpoints ──────────────────────────────────────────────────────────
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
    """Manual endpoint to force a scaling round. Delegates to autoscaler_trigger."""
    return autoscaler_trigger(request)
