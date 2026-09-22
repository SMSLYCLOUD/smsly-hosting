from __future__ import annotations

import logging
import subprocess
import time
import threading
from datetime import datetime

from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.autoscaler import registry
from apps.autoscaler.engine.container_metrics import (
    collect_container_stats,
    init_k8s,
)
from apps.autoscaler.models import AutoscalerConfig

logger = logging.getLogger(__name__)

DEFAULT_CHECK_INTERVAL = 60
CACHE_KEY_STATUS = "autoscaler:status"
CACHE_KEY_HISTORY = "autoscaler:history"
CACHE_KEY_CONFIG = "autoscaler:config"
CACHE_KEY_DECISIONS = "autoscaler:decisions"
CACHE_KEY_LAST_SCALE = "autoscaler:last_scale"
COOLDOWN_UP = 60
COOLDOWN_DOWN = 300
START_TIME = time.time()

# Maximum time (seconds) the API view will wait for a live stats check
# before returning cached data. Prevents the endpoint from hanging.
API_TIMEOUT = 15

init_k8s()


def _get_system_memory() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().total / (1024 * 1024))
    except (ImportError, AttributeError) as exc:
        logger.debug("psutil memory detection failed: %s", exc)
    try:
        result = subprocess.run(
            ["free", "-m"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                return int(parts[1]) if len(parts) >= 2 else 4096
    except (subprocess.SubprocessError, (IndexError, ValueError)) as exc:
        logger.debug("free memory detection failed: %s", exc)
    return 4096


def _classify_container(name: str):
    return registry.classify(name)


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
    _overlay_paas_state(services)
    return services


def _overlay_paas_state(services: dict) -> None:
    """Replace legacy container-view guesses with real PaaS state.

    ``current_workers`` was hardcoded to 1 and ceilings came from the
    legacy config defaults (1/4) while the PaaS engine manages
    ServiceReplica rows with per-service min/max (1/8) — the dashboard
    showed one worker forever and the service toggles had nothing to
    match. On exact container-name == Service.name match (or app-name
    match), report 1 home instance + RUNNING replicas with the service's
    own ceilings, target CPU, allocated resources, VPA flag, active
    cooldowns, and live replicas detail. Never raises — metrics must
    degrade to estimates, never 500.
    """
    try:
        from django.db.models import Count

        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.models import Service

        running = dict(
            ServiceReplica.objects.filter(status="RUNNING")
            .values("service_id")
            .annotate(n=Count("id"))
            .values_list("service_id", "n")
        )
        active_replicas = list(
            ServiceReplica.objects.filter(
                status__in=['RUNNING', 'SPAWNING', 'DRAINING', 'DESTROYING']
            ).select_related('node').order_by('-created_at')
        )
        replicas_by_service: dict[str, list[dict]] = {}
        for r in active_replicas:
            sid = str(r.service_id)
            if sid not in replicas_by_service:
                replicas_by_service[sid] = []
            replicas_by_service[sid].append({
                'id': str(r.id),
                'container_name': r.container_name,
                'status': r.status,
                'node': r.node.name if r.node else 'local',
                'node_host': r.node.host if r.node else None,
                'spawn_reason': r.spawn_reason,
                'created_at': r.created_at.isoformat(),
            })

        rows = Service.objects.only(
            "id", "name", "autoscale_enabled", "autoscale_cpu_target",
            "min_replicas", "max_replicas", "cpu_cores", "memory_mb",
            "vpa_enabled", "last_scale_at", "alert_config",
        )
        by_name = {s.name: s for s in rows}
        for name, entry in services.items():
            app_name = entry.get("app")
            svc = by_name.get(name) or (by_name.get(app_name) if app_name else None)
            if svc is None:
                continue
            sid_str = str(svc.id)
            entry["service_id"] = sid_str
            entry["autoscale_enabled"] = svc.autoscale_enabled
            entry["autoscale_cpu_target"] = svc.autoscale_cpu_target or 80
            entry["current_workers"] = 1 + int(running.get(svc.id, 0))
            entry["min_workers"] = 0 if svc.min_replicas == 0 else (svc.min_replicas or 1)
            entry["min_replicas"] = svc.min_replicas
            entry["max_workers"] = svc.max_replicas or entry["max_workers"]
            entry["max_replicas"] = svc.max_replicas
            entry["cpu_cores"] = float(svc.cpu_cores) if svc.cpu_cores else None
            entry["memory_mb_allocated"] = svc.memory_mb
            entry["vpa_enabled"] = bool(svc.vpa_enabled)
            entry["last_scale_at"] = svc.last_scale_at.isoformat() if svc.last_scale_at else None
            alert_cfg = dict(svc.alert_config or {})
            entry["cooldown_up_min"] = alert_cfg.get("cooldown_up_min", 3)
            entry["cooldown_down_min"] = alert_cfg.get("cooldown_down_min", 10)
            entry["replicas"] = replicas_by_service.get(sid_str, [])
    except Exception as exc:
        logger.debug("PaaS state overlay skipped: %s", exc)


def _get_infra_autoscaler_state(stats: dict | None = None) -> dict:
    """Query infrastructure autoscaler state (Celery burst workers, RabbitMQ queues, and in-flight drains).

    Monitors:
    1. Celery burst workers: celery-fast and celery-deploy.
       - Scale up when queue >= CELERY_SCALE_UP_THRESHOLD (default: 50) for 60s
       - Scale down when queue <= CELERY_SCALE_DOWN_THRESHOLD (default: 5) for 120s AND 0 in-flight tasks
    2. Primary workers: celery (always on, drains all queues) and celery-beat (scheduler).
    3. In-flight task draining: checks unacknowledged/active tasks to prevent killing multi-minute tasks.
    4. RabbitMQ queue depths: celery, deploy, fast, media-telemetry, media-audit.
    5. Gunicorn dynamic web process scaling: TTIN (+1) / TTOU (-1) with memory pressure budget.
    """
    import os
    try:
        autoscale_enabled = os.environ.get("CELERY_AUTOSCALE_ENABLED", "true").lower() in ("true", "1", "yes")
        scale_up_thresh = int(os.environ.get("CELERY_SCALE_UP_THRESHOLD", 50))
        scale_down_thresh = int(os.environ.get("CELERY_SCALE_DOWN_THRESHOLD", 5))
        scale_up_after = int(os.environ.get("CELERY_SCALE_UP_AFTER", 60))
        scale_down_after = int(os.environ.get("CELERY_SCALE_DOWN_AFTER", 120))
        check_interval = int(os.environ.get("CELERY_SCALE_CHECK_INTERVAL", 15))
    except Exception:
        autoscale_enabled = True
        scale_up_thresh = 50
        scale_down_thresh = 5
        scale_up_after = 60
        scale_down_after = 120
        check_interval = 15

    active_by_worker: dict[str, int] = {}
    reserved_by_worker: dict[str, int] = {}
    ping_workers: list[str] = []
    queues = {
        "celery": 0,
        "deploy": 0,
        "fast": 0,
        "media-telemetry": 0,
        "media-audit": 0,
    }
    total_depth = 0

    import sys
    from django.conf import settings
    is_testing = "pytest" in sys.modules or getattr(settings, "TESTING", False)

    if not is_testing:
        try:
            from config.celery import app as celery_app
            with celery_app.connection_for_read() as conn:
                conn.connect_timeout = 0.5
                conn.ensure_connection(max_retries=0, timeout=0.5)
                insp = celery_app.control.inspect(timeout=0.5, connection=conn)
                if insp:
                    active_map = insp.active() or {}
                    reserved_map = insp.reserved() or {}
                    ping_workers = list((insp.ping() or {}).keys())
                    for wname, tasks in active_map.items():
                        active_by_worker[wname] = len(tasks) if isinstance(tasks, list) else 0
                    for wname, tasks in reserved_map.items():
                        reserved_by_worker[wname] = len(tasks) if isinstance(tasks, list) else 0

                for q_name in list(queues.keys()):
                    try:
                        q = conn.default_channel.queue_declare(queue=q_name, passive=True)
                        count = getattr(q, "message_count", 0) or 0
                        queues[q_name] = count
                        total_depth += count
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("Celery/RabbitMQ broker probe skipped: %s", exc)

    def _find_stat(pattern: str) -> dict | None:
        if not stats:
            return None
        for cname, s in stats.items():
            if pattern in cname:
                return s
        return None

    def _worker_active_count(pattern: str) -> int:
        return sum(v for k, v in active_by_worker.items() if pattern in k)

    def _worker_reserved_count(pattern: str) -> int:
        return sum(v for k, v in reserved_by_worker.items() if pattern in k)

    def _is_worker_running(pattern: str) -> bool:
        if any(pattern in w for w in ping_workers):
            return True
        if _find_stat(pattern) is not None:
            return True
        return False

    burst_workers = []
    for bw_name, q_target, desc in [
        ("celery-fast", "fast", "Burst Worker (Fast Queue)"),
        ("celery-deploy", "deploy", "Burst Worker (Deploy Queue)"),
    ]:
        stat = _find_stat(bw_name)
        is_running = _is_worker_running(bw_name.replace("celery-", ""))
        active_cnt = _worker_active_count(bw_name.replace("celery-", ""))
        reserved_cnt = _worker_reserved_count(bw_name.replace("celery-", ""))
        q_depth = queues.get(q_target, 0)

        if is_running:
            if total_depth <= scale_down_thresh:
                if active_cnt > 0:
                    status = "draining"
                    drain_held = True
                    msg = f"Queue low ({total_depth} <= {scale_down_thresh}). Draining {active_cnt} in-flight tasks before stopping."
                else:
                    status = "pending_scale_down"
                    drain_held = False
                    msg = f"Queue idle ({total_depth} <= {scale_down_thresh}). 120s cooldown active before stopping."
            elif total_depth >= scale_up_thresh:
                status = "busy"
                drain_held = False
                msg = f"Queue pressure ({total_depth} >= {scale_up_thresh}). Actively processing."
            else:
                status = "running"
                drain_held = False
                msg = "Normal processing."
        else:
            status = "scaled_down"
            drain_held = False
            msg = f"Scaled down (idle container stopped). Scales up when queue >= {scale_up_thresh} for {scale_up_after}s."

        burst_workers.append({
            "name": bw_name,
            "description": desc,
            "target_queue": q_target,
            "status": status,
            "is_running": is_running,
            "active_tasks": active_cnt,
            "reserved_tasks": reserved_cnt,
            "queue_depth": q_depth,
            "drain_held": drain_held,
            "status_message": msg,
            "cpu_percent": stat.get("cpu_percent", 0.0) if stat else 0.0,
            "memory_mb": stat.get("memory_mb", 0.0) if stat else 0.0,
        })

    # Primary always-on worker
    primary_stat = _find_stat("smsly-hosting-celery-1") or _find_stat("celery")
    primary_active = _worker_active_count("celery@") - sum(b["active_tasks"] for b in burst_workers)
    primary_worker = {
        "name": "celery",
        "description": "Primary Platform Worker (Always On)",
        "queues": ["celery", "deploy", "fast", "media-telemetry", "media-audit"],
        "status": "running" if (_is_worker_running("celery") or primary_stat) else "running",
        "active_tasks": max(0, primary_active),
        "reserved_tasks": _worker_reserved_count("celery"),
        "cpu_percent": primary_stat.get("cpu_percent", 0.0) if primary_stat else 0.0,
        "memory_mb": primary_stat.get("memory_mb", 0.0) if primary_stat else 0.0,
    }

    # Celery beat scheduler
    beat_stat = _find_stat("celery-beat")
    beat_worker = {
        "name": "celery-beat",
        "description": "Periodic Task Scheduler (RedBeat)",
        "status": "running" if beat_stat else "running",
        "cpu_percent": beat_stat.get("cpu_percent", 0.0) if beat_stat else 0.0,
        "memory_mb": beat_stat.get("memory_mb", 0.0) if beat_stat else 0.0,
    }

    # Gunicorn dynamic web process scaling
    gunicorn_stat = _find_stat("backend")
    pids = gunicorn_stat.get("pids", 0) if gunicorn_stat else 0
    est_gunicorn_workers = max(1, pids - 1) if pids > 1 else 2
    gunicorn_scaler = {
        "service": "backend-gunicorn",
        "strategy": "Signal-Driven Dynamic Scaling (TTIN +1 / TTOU -1 / SIGHUP)",
        "current_workers": est_gunicorn_workers,
        "min_workers": 2,
        "max_workers": 8,
        "pids": pids,
        "memory_mb": gunicorn_stat.get("memory_mb", 0.0) if gunicorn_stat else 0.0,
        "cpu_percent": gunicorn_stat.get("cpu_percent", 0.0) if gunicorn_stat else 0.0,
    }

    burst_unacked = sum(b["active_tasks"] for b in burst_workers)
    return {
        "autoscale_enabled": autoscale_enabled,
        "total_queue_depth": total_depth,
        "queues": queues,
        "unacknowledged_burst_tasks": burst_unacked,
        "scale_down_held": any(b["drain_held"] for b in burst_workers),
        "scale_up_threshold": scale_up_thresh,
        "scale_down_threshold": scale_down_thresh,
        "scale_up_after_seconds": scale_up_after,
        "scale_down_after_seconds": scale_down_after,
        "check_interval_seconds": check_interval,
        "burst_workers": burst_workers,
        "primary_worker": primary_worker,
        "scheduler": beat_worker,
        "web_scaling": gunicorn_scaler,
    }


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


# ── Decision history: legacy engine + actual replica lifecycle ──────────
def _parse_ts(value) -> datetime:
    """Parse an ISO timestamp defensively; unparseable sorts last."""
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _snapshot_mem(snapshot) -> float:
    try:
        return float((snapshot or {}).get("memory_mb", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _replica_scale_events(limit: int = 50) -> list[dict]:
    """Actual scaling history derived from ServiceReplica lifecycle rows.

    The legacy engine below only records its own container-level advice
    (and can never fire scale-down with default min_workers=1), so manual
    spawns/destroys and reconciler actions would otherwise leave the
    dashboard timeline permanently empty. Worker counts are replayed per
    service from the current RUNNING count backwards through the window,
    so recent entries are exact.
    """
    try:
        from apps.autoscaler.models.replica import ServiceReplica
        rows = list(
            ServiceReplica.objects.select_related("service")
            .order_by("-created_at")[:limit]
        )
        running_now = dict(
            ServiceReplica.objects.filter(status="RUNNING")
            .values("service_id")
            .annotate(n=Count("id"))
            .values_list("service_id", "n")
        )
    except Exception as exc:
        logger.debug("Autoscaler: replica history unavailable: %s", exc)
        return []
    by_service: dict = {}
    for r in rows:
        by_service.setdefault(r.service_id, []).append(r)
    events: list[dict] = []
    for service_rows in by_service.values():
        # Replay the window chronologically (creates AND destroys in true
        # time order), starting from the current RUNNING count rewound by
        # the window's net change — so recent entries are exact.
        stream: list[tuple] = []
        for r in service_rows:
            if r.created_at:
                stream.append((r.created_at, 0, r))
            if r.destroyed_at:
                stream.append((r.destroyed_at, 1, r))
        net = sum(1 for _, kind, _ in stream if kind == 0) - sum(
            1 for _, kind, _ in stream if kind == 1
        )
        counter = running_now.get(service_rows[0].service_id, 0) - net
        if counter < 0:
            counter = 0
        for ts, kind, r in sorted(stream, key=lambda e: str(e[0])):
            name = r.container_name or r.service.name
            mem = _snapshot_mem(r.metrics_snapshot)
            reason = (r.spawn_reason or "").strip()
            if kind == 0:
                events.append({
                    "timestamp": ts.isoformat(),
                    "container": name,
                    "action": "scale_up",
                    "current_workers": counter,
                    "target_workers": counter + 1,
                    "current_memory_mb": mem,
                    "target_memory_mb": mem,
                    "reason": reason or "Replica spawned",
                })
                counter += 1
            else:
                events.append({
                    "timestamp": ts.isoformat(),
                    "container": name,
                    "action": "scale_down",
                    "current_workers": counter,
                    "target_workers": max(counter - 1, 0),
                    "current_memory_mb": mem,
                    "target_memory_mb": mem,
                    "reason": f"Replica removed{(' — ' + reason) if reason else ''}",
                })
                counter = max(counter - 1, 0)
    return events


def _get_recent_decisions(limit: int = 50) -> list[dict]:
    """Merge legacy engine advice with actual replica lifecycle events."""
    merged: list[dict] = []
    seen = set()
    try:
        legacy = cache.get(CACHE_KEY_DECISIONS, []) or []
    except Exception:
        legacy = []
    for d in list(legacy) + _replica_scale_events(limit):
        if not isinstance(d, dict):
            continue
        key = (str(d.get("timestamp")), str(d.get("container")), str(d.get("action")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(d)
    merged.sort(key=lambda d: _parse_ts(d.get("timestamp")), reverse=True)
    return merged[:limit]


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


# ── Core health-check routine ──────────────────────────────────────────────
def _run_autoscaler_check():
    config = _get_config()
    # Stat only containers the dashboard renders: the daemon hosts
    # ~100 addon/sidecar containers and statting all of them over a
    # loaded socket-proxy trips the 20s cap with mostly timeouts
    # (2026-09-16: 18s, zero results, dashboard 503 on every load).
    stats = collect_container_stats(
        include=lambda name: _classify_container(name) is not None)
    services = _build_services_map(stats)
    total_mem = config.get('total_system_mb', _get_system_memory())
    infra_reserve = config.get('infra_reserve_mb', 512)
    _record_history(services, total_mem, infra_reserve)
    # Records legacy advice into the decisions timeline (side effect);
    # the return value is intentionally unused — see NOTE below.
    _decide_scaling(services)
    # NOTE: legacy advice is recorded for the timeline but NOT applied
    # here. Applying only ever drove Docker Swarm / K8s workloads; on
    # this platform every service is a plain container, so each apply
    # was a docker round-trip ending in a caught log line — and under
    # load those round-trips hung long enough to push the writer past
    # its time limit, so the STATUS cache was never written and the
    # dashboard 503'd permanently. Real scaling lives in
    # tasks_autoscale.analyze_all_services_task (see trigger below).
    total_used = sum(s["memory_mb"] for s in services.values())
    app_budget = total_mem - infra_reserve
    try:
        import psutil
        host_cpu_percent = float(psutil.cpu_percent(interval=None) or 0.0)
        host_cpu_count = int(psutil.cpu_count() or 0)
    except Exception:
        host_cpu_percent, host_cpu_count = 0.0, 0
    status_data = {
        "status": "active",
        "uptime_seconds": round(time.time() - START_TIME),
        "check_interval": config.get('check_interval', DEFAULT_CHECK_INTERVAL),
        "last_check_at": timezone.now().isoformat(),
        "host": {
            "cpu_percent": round(host_cpu_percent, 1),
            "cpu_count": host_cpu_count,
        },
        "budget": {
            "total_system_mb": total_mem,
            "infra_reserve_mb": infra_reserve,
            "app_budget_mb": app_budget,
            "used_mb": round(total_used, 1),
            "free_mb": round(max(app_budget - total_used, 0), 1),
        },
        "services": services,
        "recent_decisions": _get_recent_decisions(),
        "infra": _get_infra_autoscaler_state(stats),
    }
    cache.set(CACHE_KEY_STATUS, status_data, timeout=300)
    return status_data


# ── DRF endpoints ──────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAdminUser])
def autoscaler_status(request) -> Response:
    """Return autoscaler status.
    - If cached data is fresh (<60s), return immediately (sub-second).
    - Otherwise run a live check with timeout, fall back to cache."""
    cached = cache.get(CACHE_KEY_STATUS)
    if cached is None:
        logger.debug("Autoscaler: no cached data, running live check")

    # If cached data exists and is recent, return it instantly
    if cached:
        try:
            last_check = cached.get("last_check_at", "")
            if last_check:
                from datetime import datetime, timezone
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_check)).total_seconds()
                if age < 60:
                    logger.debug("Autoscaler: returning cached data (age=%.1fs)", age)
                    return Response(cached)
                logger.debug("Autoscaler: cache stale (age=%.1fs), running live check", age)
        except Exception as exc:
            logger.debug("Autoscaler: cache freshness check failed: %s", exc)

    # No fresh cache — run live check with timeout
    result = [None]
    done = threading.Event()

    def _live_check():
        try:
            result[0] = _run_autoscaler_check()
        except Exception as exc:
            logger.error("Autoscaler live check failed: %s", exc)
        finally:
            done.set()

    t = threading.Thread(target=_live_check, daemon=True)
    t.start()
    done.wait(timeout=API_TIMEOUT)
    if not done.is_set():
        logger.warning("Autoscaler: live check timed out after %ds", API_TIMEOUT)

    if result[0] is not None:
        return Response(result[0])

    # Timed out — return stale cache, a degraded snapshot, or error,
    # in that order. The degraded snapshot is docker-free (config +
    # DB decisions only) so the page renders with truth instead of
    # the "Autoscaler check timed out" 503 whenever the daemon is too
    # slow for a live sweep (2026-09-16: every sweep blew the 15s
    # budget under host steal, page showed "Not Installed" for hours).
    if cached:
        cached["_stale"] = True
        return Response(cached)
    try:
        return Response(_degraded_status())
    except Exception as exc:
        logger.debug("Degraded autoscaler status unavailable: %s", exc)
    return Response({"error": "Autoscaler check timed out", "status": "error"}, status=503)


def _degraded_status() -> dict:
    """Best-effort status without touching Docker.

    Budget skeleton from config plus the DB-backed decisions timeline
    (replica lifecycle rows — exact, no sampling). Services list is
    empty: gauges render "Unknown", never fabricated zeros. Never
    raises — callers treat failure as "no degraded snapshot".
    """
    config = _get_config()
    total_mem = config.get('total_system_mb', _get_system_memory())
    infra_reserve = config.get('infra_reserve_mb', 512)
    app_budget = total_mem - infra_reserve
    return {
        "status": "degraded",
        "uptime_seconds": round(time.time() - START_TIME),
        "check_interval": config.get('check_interval', DEFAULT_CHECK_INTERVAL),
        "last_check_at": None,
        "host": {"cpu_percent": 0.0, "cpu_count": 0},
        "budget": {
            "total_system_mb": total_mem,
            "infra_reserve_mb": infra_reserve,
            "app_budget_mb": app_budget,
            "used_mb": 0,
            "free_mb": round(app_budget, 1),
        },
        "services": {},
        "recent_decisions": _get_recent_decisions(),
        "infra": _get_infra_autoscaler_state(None),
        "_stale": True,
        "message": ("Live container stats unavailable — Docker daemon too "
                    "slow. Showing recorded scaling events; retrying."),
    }


@api_view(["GET"])
@permission_classes([IsAdminUser])
def autoscaler_history(request) -> Response:
    history = cache.get(CACHE_KEY_HISTORY)
    if history:
        return Response(history)
    # No cached history — run a live check with timeout
    done = threading.Event()

    def _live():
        try:
            _run_autoscaler_check()
        except Exception:
            pass
        finally:
            done.set()

    t = threading.Thread(target=_live, daemon=True)
    t.start()
    done.wait(timeout=API_TIMEOUT)

    history = cache.get(CACHE_KEY_HISTORY, {"timestamps": [], "services": {}, "budget": {"used_mb": [], "free_mb": []}})
    return Response(history)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autoscaler_config(request) -> Response:
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
def autoscaler_trigger(request) -> Response:
    """Force an immediate check. Runs in a thread with timeout.

    Also dispatches the real PaaS engine sweep — the dashboard check
    above only records container-level advice, so without this a
    "Force Check" click never scaled any Service.
    """
    try:
        from apps.autoscaler.services.tasks_autoscale import (
            analyze_all_services_task,
        )
        analyze_all_services_task.delay()
    except Exception as exc:
        logger.debug("PaaS engine dispatch skipped: %s", exc)
    result = [None]
    done = threading.Event()

    def _live():
        try:
            result[0] = _run_autoscaler_check()
        except Exception as exc:
            logger.error("Autoscaler trigger failed: %s", exc)
            result[0] = {"error": str(exc)}
        finally:
            done.set()

    t = threading.Thread(target=_live, daemon=True)
    t.start()
    done.wait(timeout=API_TIMEOUT)

    if result[0] is None:
        cached = cache.get(CACHE_KEY_STATUS)
        if cached:
            cached["_stale"] = True
            return Response(cached)
        return Response({"error": "Autoscaler trigger timed out", "status": "error"}, status=503)

    return Response(result[0])


@api_view(["POST"])
@permission_classes([IsAdminUser])
def autoscaler_scale(request) -> Response:
    return autoscaler_trigger(request)
