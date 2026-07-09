#!/usr/bin/env python3
"""
SMSLY VPS Autoscaler — Cross-Service Resource Manager

Runs as a systemd service on the VPS. Every 30s it:
1. Reads Docker stats for ALL smsly containers
2. Calculates system-wide memory pressure
3. Identifies which services are under load (traffic/CPU)
4. Dynamically adjusts:
   - Gunicorn workers (via SIGHUP)
   - Celery concurrency (via pool_resize)
   - Docker memory limits (via docker update)

Install:
  cp smsly-autoscaler.py /opt/smsly/autoscaler.py
  cp smsly-autoscaler.service /etc/systemd/system/
  systemctl enable --now smsly-autoscaler

Architecture:
  - Global memory budget (e.g. 10GB for all apps)
  - Each service gets a share proportional to its current demand
  - Idle services shrink to minimum, busy ones expand
  - Hard floor: each service always gets at least 256MB
"""

import collections
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [autoscaler] %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/var/log/smsly-autoscaler.log')
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Total memory budget for all app containers (in MB)
# Reserve ~2GB for OS + shared infra (Postgres, Redis, Ollama)
TOTAL_SYSTEM_MB = int(os.environ.get('AUTOSCALER_TOTAL_MB', '10240'))  # 10GB default
INFRA_RESERVE_MB = int(os.environ.get('AUTOSCALER_INFRA_RESERVE_MB', '2048'))  # 2GB for OS+infra
APP_BUDGET_MB = TOTAL_SYSTEM_MB - INFRA_RESERVE_MB

# Minimum memory per container (MB)
MIN_MEMORY_MB = 256
# Maximum memory per worker container (MB)
MAX_MEMORY_MB = 2048

# Check interval (seconds)
CHECK_INTERVAL = int(os.environ.get('AUTOSCALER_INTERVAL', '30'))

# Gunicorn worker memory (MB per worker)
WORKER_MEMORY_MB = 120

# API Port
API_PORT = int(os.environ.get('AUTOSCALER_API_PORT', '9876'))

# API auth token — POST endpoints require this. Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
API_TOKEN = os.environ.get('AUTOSCALER_API_TOKEN', '')
if not API_TOKEN:
    logger.error("AUTOSCALER_API_TOKEN is required; refusing to start autoscaler without auth token")
    sys.exit(1)

# Container groups — maps container names to their roles
SERVICE_GROUPS = {
    # smsly-helper
    'smsly-helper-web': {'type': 'gunicorn', 'app': 'smsly-helper', 'priority': 3, 'min_workers': 2, 'max_workers': 8},
    'smsly-helper-celery': {'type': 'celery', 'app': 'smsly-helper', 'priority': 2, 'min_workers': 1, 'max_workers': 4},

    # lina-deluxe
    'lina-deluxe-backend': {'type': 'gunicorn', 'app': 'lina-deluxe', 'priority': 2, 'min_workers': 2, 'max_workers': 4},
    'lina-deluxe-celery': {'type': 'celery', 'app': 'lina-deluxe', 'priority': 2, 'min_workers': 1, 'max_workers': 4},

    # buyforfront
    'buyforfront-backend': {'type': 'daphne', 'app': 'buyforfront', 'priority': 2, 'min_workers': 1, 'max_workers': 1},

    # smsly-marketer
    'ignite-web': {'type': 'gunicorn', 'app': 'marketer', 'priority': 1, 'min_workers': 2, 'max_workers': 4},
    'ignite-celery': {'type': 'celery', 'app': 'marketer', 'priority': 1, 'min_workers': 1, 'max_workers': 4},
}

# State history for API
HISTORY = collections.deque(maxlen=120)  # 1 hour at 30s intervals
HISTORY_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
LATEST_STATE = {}


@dataclass
class ContainerStats:
    name: str
    cpu_percent: float
    memory_mb: float
    memory_limit_mb: float
    memory_percent: float
    net_rx_mb: float
    net_tx_mb: float
    pids: int


@dataclass
class ScalingDecision:
    container: str
    action: str  # 'scale_up', 'scale_down', 'adjust_memory', 'none'
    current_workers: int
    target_workers: int
    current_memory_mb: float
    target_memory_mb: float
    reason: str
    timestamp: str = ""  # Added for API response


# =============================================================================
# Docker Stats Collection
# =============================================================================

def get_docker_stats() -> dict[str, ContainerStats]:
    """Read live Docker stats for all running containers."""
    try:
        result = subprocess.run(
            ['docker', 'stats', '--no-stream',
             '--format', '{{json .}}'],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            logger.error(f"docker stats failed: {result.stderr}")
            return {}

        stats = {}
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                name = data.get('Name', '')

                # Parse CPU (e.g. "2.34%")
                cpu_str = data.get('CPUPerc', '0%').replace('%', '')
                cpu = float(cpu_str) if cpu_str else 0.0

                # Parse memory (e.g. "256MiB / 512MiB")
                mem_usage = data.get('MemUsage', '0B / 0B')
                parts = mem_usage.split(' / ')
                mem_used = _parse_size_mb(parts[0].strip()) if len(parts) >= 1 else 0
                mem_limit = _parse_size_mb(parts[1].strip()) if len(parts) >= 2 else 0

                mem_pct_str = data.get('MemPerc', '0%').replace('%', '')
                mem_pct = float(mem_pct_str) if mem_pct_str else 0.0

                # Parse network I/O (e.g. "1.2MB / 3.4MB")
                net_io = data.get('NetIO', '0B / 0B')
                net_parts = net_io.split(' / ')
                net_rx = _parse_size_mb(net_parts[0].strip()) if len(net_parts) >= 1 else 0
                net_tx = _parse_size_mb(net_parts[1].strip()) if len(net_parts) >= 2 else 0

                pids = int(data.get('PIDs', '0'))

                stats[name] = ContainerStats(
                    name=name,
                    cpu_percent=cpu,
                    memory_mb=mem_used,
                    memory_limit_mb=mem_limit,
                    memory_percent=mem_pct,
                    net_rx_mb=net_rx,
                    net_tx_mb=net_tx,
                    pids=pids
                )
            except (json.JSONDecodeError, ValueError, IndexError) as e:
                logger.debug(f"Skipping malformed stats line: {e}")
                continue

        return stats

    except subprocess.TimeoutExpired:
        logger.error("docker stats timed out")
        return {}
    except FileNotFoundError:
        logger.error("docker not found — is Docker installed?")
        return {}


def _parse_size_mb(size_str: str) -> float:
    """Convert Docker size string (e.g. '256MiB', '1.2GiB') to MB."""
    size_str = size_str.strip().upper()
    try:
        if 'GIB' in size_str or 'GB' in size_str:
            return float(size_str.replace('GIB', '').replace('GB', '').strip()) * 1024
        elif 'MIB' in size_str or 'MB' in size_str:
            return float(size_str.replace('MIB', '').replace('MB', '').strip())
        elif 'KIB' in size_str or 'KB' in size_str:
            return float(size_str.replace('KIB', '').replace('KB', '').strip()) / 1024
        elif 'B' in size_str:
            return float(size_str.replace('B', '').strip()) / (1024 * 1024)
    except ValueError:
        pass
    return 0.0


# =============================================================================
# Scaling Logic
# =============================================================================

def calculate_demand_scores(stats: dict[str, ContainerStats]) -> dict[str, float]:
    """
    Calculate a demand score [0-1] for each managed container.
    Higher = more resources needed.

    Factors:
    - CPU usage (40% weight)
    - Memory pressure (30% weight)
    - Network I/O rate (20% weight)
    - Process count / PID pressure (10% weight)
    """
    scores = {}

    for name, config in SERVICE_GROUPS.items():
        if name not in stats:
            scores[name] = 0.0
            continue

        s = stats[name]

        # CPU score: 0% → 0, 100%+ → 1
        cpu_score = min(s.cpu_percent / 100.0, 1.0)

        # Memory pressure: usage relative to limit
        mem_score = min(s.memory_percent / 100.0, 1.0)

        # Network score (normalized to ~50MB/s as "max")
        net_score = min((s.net_rx_mb + s.net_tx_mb) / 50.0, 1.0)

        # PID score (normalized to ~100 as "max")
        pid_score = min(s.pids / 100.0, 1.0)

        total = (cpu_score * 0.4) + (mem_score * 0.3) + (net_score * 0.2) + (pid_score * 0.1)

        # Weight by priority (higher priority services get boosted scores)
        priority_boost = 1.0 + (config['priority'] - 1) * 0.1
        scores[name] = min(total * priority_boost, 1.0)

    return scores


def make_scaling_decisions(
    stats: dict[str, ContainerStats],
    demand_scores: dict[str, float]
) -> list[ScalingDecision]:
    """
    Decide how to redistribute resources based on demand.

    Strategy:
    - Total worker budget = APP_BUDGET_MB / WORKER_MEMORY_MB
    - Distribute workers proportional to demand scores
    - Ensure min/max constraints
    - Scale memory limits with worker count
    """
    decisions = []
    now_iso = datetime.now(UTC).isoformat()

    # Calculate total demand
    total_demand = sum(demand_scores.values())
    if total_demand == 0:
        total_demand = 1.0  # Avoid division by zero

    for name, config in SERVICE_GROUPS.items():
        if name not in stats:
            continue

        s = stats[name]
        demand = demand_scores.get(name, 0.0)

        if config['type'] == 'daphne':
            # Daphne is single-process — just adjust memory
            demand_share = demand / total_demand
            target_mem = max(MIN_MEMORY_MB, min(
                int(APP_BUDGET_MB * demand_share * 0.5),  # Cap at 50% of share
                MAX_MEMORY_MB
            ))
            decisions.append(ScalingDecision(
                container=name,
                action='adjust_memory' if abs(target_mem - s.memory_limit_mb) > 64 else 'none',
                current_workers=1,
                target_workers=1,
                current_memory_mb=s.memory_limit_mb,
                target_memory_mb=target_mem,
                reason=f'demand={demand:.2f}',
                timestamp=now_iso
            ))
            continue

        # For gunicorn/celery — adjust worker count
        demand_share = demand / total_demand

        # Workers proportional to demand
        max_workers = config['max_workers']
        min_workers = config['min_workers']

        if demand < 0.1:
            # Very low demand — scale to minimum
            target_workers = min_workers
        elif demand < 0.3:
            # Low demand — stay near minimum
            target_workers = min(min_workers + 1, max_workers)
        elif demand < 0.6:
            # Medium demand — mid-range
            target_workers = min(int(max_workers * 0.6), max_workers)
        else:
            # High demand — scale up
            target_workers = max_workers

        # Current workers estimated from PIDs
        # Gunicorn: 1 master + N workers
        # Celery: 1 main + N pool workers
        current_workers = max(1, s.pids - 1)

        target_mem = max(MIN_MEMORY_MB, target_workers * WORKER_MEMORY_MB + 128)  # +128 for overhead
        target_mem = min(target_mem, MAX_MEMORY_MB)

        action = 'none'
        if target_workers > current_workers:
            action = 'scale_up'
        elif target_workers < current_workers:
            action = 'scale_down'
        elif abs(target_mem - s.memory_limit_mb) > 64:
            action = 'adjust_memory'

        decisions.append(ScalingDecision(
            container=name,
            action=action,
            current_workers=current_workers,
            target_workers=target_workers,
            current_memory_mb=s.memory_limit_mb,
            target_memory_mb=target_mem,
            reason=f'demand={demand:.2f}, cpu={s.cpu_percent:.1f}%, mem={s.memory_percent:.1f}%',
            timestamp=now_iso
        ))

    return decisions


# =============================================================================
# Scaling Actions
# =============================================================================

def apply_decisions(decisions: list[ScalingDecision]):
    """Apply scaling decisions to running containers."""
    for d in decisions:
        if d.action == 'none':
            continue

        config = SERVICE_GROUPS.get(d.container, {})
        container_type = config.get('type', 'unknown')

        logger.info(
            f"[{d.container}] {d.action}: "
            f"workers {d.current_workers}→{d.target_workers}, "
            f"memory {d.current_memory_mb:.0f}MB→{d.target_memory_mb:.0f}MB "
            f"({d.reason})"
        )

        try:
            # Adjust memory limit
            if d.action in ('adjust_memory', 'scale_up', 'scale_down'):
                subprocess.run(
                    ['docker', 'update',
                     '--memory', f'{int(d.target_memory_mb)}m',
                     '--memory-swap', f'{int(d.target_memory_mb * 1.5)}m',
                     d.container],
                    capture_output=True, timeout=10
                )

            # Scale workers
            if d.action in ('scale_up', 'scale_down'):
                if container_type == 'gunicorn':
                    _scale_gunicorn(d.container, d.target_workers)
                elif container_type == 'celery':
                    _scale_celery(d.container, d.target_workers)

        except Exception as e:
            logger.error(f"Failed to apply scaling for {d.container}: {e}")


def _scale_gunicorn(container: str, target_workers: int):
    """
    Scale Gunicorn by writing the target worker count and sending SIGHUP.
    SIGHUP causes Gunicorn to gracefully adjust its worker pool.
    We use TTIN/TTOU signals for precise control.
    """
    try:
        # Get current worker count
        result = subprocess.run(
            ['docker', 'exec', container, 'sh', '-c',
             'pgrep -c -P 1 gunicorn || echo 0'],
            capture_output=True, text=True, timeout=10
        )
        current = int(result.stdout.strip()) if result.returncode == 0 else 0

        if target_workers > current:
            # Scale up: send TTIN for each new worker
            for _ in range(target_workers - current):
                subprocess.run(
                    ['docker', 'exec', container, 'kill', '-TTIN', '1'],
                    capture_output=True, timeout=5
                )
                time.sleep(0.5)
        elif target_workers < current:
            # Scale down: send TTOU for each worker to remove
            for _ in range(current - target_workers):
                subprocess.run(
                    ['docker', 'exec', container, 'kill', '-TTOU', '1'],
                    capture_output=True, timeout=5
                )
                time.sleep(0.5)

        logger.info(f"[{container}] Gunicorn scaled: {current}→{target_workers} workers")

    except Exception as e:
        logger.error(f"[{container}] Gunicorn scaling failed: {e}")


def _scale_celery(container: str, target_workers: int):
    """
    Scale Celery pool using the inspect/control interface.
    Celery --autoscale handles this mostly, but we can force pool resize.
    """
    try:
        subprocess.run(
            ['docker', 'exec', container,
             'celery', '-A', 'config', 'control', 'pool_resize',
             str(target_workers)],
            capture_output=True, timeout=15
        )
        logger.info(f"[{container}] Celery pool resized to {target_workers}")
    except Exception as e:
        # Fallback: celery autoscale handles it anyway
        logger.warning(f"[{container}] Celery pool_resize failed (autoscale will handle): {e}")


# =============================================================================
# HTTP API Server
# =============================================================================

class AutoscalerAPIHandler(BaseHTTPRequestHandler):
    def log_request(self, code='-', size='-'):
        """Suppress per-request logging to avoid log spam."""
        pass

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def _check_auth(self) -> bool:
        """Verify bearer token on mutating endpoints."""
        if not API_TOKEN:
            # Fail-closed: refuse mutating operations when no token is set.
            # GET /api/status is unauthenticated; POST /api/config and
            # POST /api/trigger require explicit token configuration.
            if self.command in ('GET', 'HEAD'):
                return True
            self._send_json({'error': 'AUTOSCALER_API_TOKEN is not configured. Set it in the environment.'}, 500)
            return False
        auth_header = self.headers.get('Authorization', '')
        if auth_header == f'Bearer {API_TOKEN}':
            return True
        self._send_json({'error': 'Unauthorized'}, 401)
        return False

    def do_GET(self):
        if self.path == '/api/status':
            with HISTORY_LOCK:
                # Construct status from latest state
                if not LATEST_STATE:
                    self._send_json({'status': 'initializing'}, 200)
                    return

                # Enrich service data with current config
                services_data = {}
                stats = LATEST_STATE.get('stats', {})
                decisions = LATEST_STATE.get('decisions', [])
                demand_scores = LATEST_STATE.get('demand_scores', {})

                for name, config in SERVICE_GROUPS.items():
                    s = stats.get(name)
                    if not s:
                        continue

                    # Find last decision for this service
                    last_decision = next((d for d in decisions if d.container == name), None)

                    services_data[name] = {
                        **config,
                        'status': 'running',
                        'demand_score': demand_scores.get(name, 0.0),
                        'cpu_percent': s.cpu_percent,
                        'memory_mb': s.memory_mb,
                        'memory_limit_mb': s.memory_limit_mb,
                        'memory_percent': s.memory_percent,
                        'net_rx_mb': s.net_rx_mb,
                        'net_tx_mb': s.net_tx_mb,
                        'pids': s.pids,
                        'current_workers': last_decision.current_workers if last_decision else 0,
                        'last_action': last_decision.action if last_decision else 'none',
                        'last_action_at': last_decision.timestamp if last_decision else None
                    }

                total_used = sum(s.memory_mb for s in stats.values() if s.name in SERVICE_GROUPS)

                response = {
                    "status": "running",
                    "uptime_seconds": int(time.time() - START_TIME),
                    "check_interval": CHECK_INTERVAL,
                    "last_check_at": LATEST_STATE.get('timestamp'),
                    "budget": {
                        "total_system_mb": TOTAL_SYSTEM_MB,
                        "infra_reserve_mb": INFRA_RESERVE_MB,
                        "app_budget_mb": APP_BUDGET_MB,
                        "used_mb": total_used,
                        "free_mb": max(0, APP_BUDGET_MB - total_used)
                    },
                    "services": services_data,
                    "recent_decisions": [asdict(d) for d in decisions if d.action != 'none']
                }
                self._send_json(response)

        elif self.path.startswith('/api/history'):
            try:
                # Parse query params
                query = self.path.split('?')[1] if '?' in self.path else ''
                params = dict(q.split('=') for q in query.split('&') if '=' in q)
                minutes = int(params.get('minutes', 60))
            except ValueError:
                minutes = 60

            limit = minutes * 2  # 2 checks per minute

            with HISTORY_LOCK:
                history_slice = list(HISTORY)[-limit:]

                timestamps = [h['timestamp'] for h in history_slice]
                services_ts = {}
                budget_used = []
                budget_free = []

                for h in history_slice:
                    stats = h['stats']
                    scores = h['demand_scores']
                    decs = h['decisions']

                    # Aggregate budget
                    used = sum(s.memory_mb for s in stats.values() if s.name in SERVICE_GROUPS)
                    budget_used.append(used)
                    budget_free.append(max(0, APP_BUDGET_MB - used))

                    # Aggregate per-service
                    for name in SERVICE_GROUPS:
                        if name not in services_ts:
                            services_ts[name] = {'cpu': [], 'memory_mb': [], 'demand_score': [], 'workers': []}

                        s = stats.get(name)
                        score = scores.get(name, 0.0)
                        d = next((x for x in decs if x.container == name), None)

                        services_ts[name]['cpu'].append(s.cpu_percent if s else 0)
                        services_ts[name]['memory_mb'].append(s.memory_mb if s else 0)
                        services_ts[name]['demand_score'].append(score)
                        services_ts[name]['workers'].append(d.current_workers if d else 0)

                response = {
                    "timestamps": timestamps,
                    "services": services_ts,
                    "budget": {
                        "used_mb": budget_used,
                        "free_mb": budget_free
                    }
                }
                self._send_json(response)

        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/config':
            if not self._check_auth():
                return
            content_len = int(self.headers.get('Content-Length', 0))
            post_body = self.rfile.read(content_len)
            try:
                new_config = json.loads(post_body)

                # Update globals
                global TOTAL_SYSTEM_MB, INFRA_RESERVE_MB, CHECK_INTERVAL, APP_BUDGET_MB
                if 'total_system_mb' in new_config:
                    TOTAL_SYSTEM_MB = int(new_config['total_system_mb'])
                if 'infra_reserve_mb' in new_config:
                    INFRA_RESERVE_MB = int(new_config['infra_reserve_mb'])
                if 'check_interval' in new_config:
                    CHECK_INTERVAL = int(new_config['check_interval'])

                APP_BUDGET_MB = TOTAL_SYSTEM_MB - INFRA_RESERVE_MB

                if 'services' in new_config:
                    for name, cfg in new_config['services'].items():
                        if name in SERVICE_GROUPS:
                            SERVICE_GROUPS[name].update(cfg)

                self._send_json({'status': 'updated'})
            except Exception as e:
                self._send_json({'error': str(e)}, 400)

        elif self.path == '/api/trigger':
            if not self._check_auth():
                return
            # Force run, then return latest status
            try:
                run_once()
                self.path = '/api/status'
                self.do_GET()
            except Exception as e:
                self._send_json({'error': str(e)}, 500)

        else:
            self.send_error(404)


def run_api_server():
    bind_addr = os.environ.get('AUTOSCALER_API_BIND', '127.0.0.1')
    server_address = (bind_addr, API_PORT)
    httpd = HTTPServer(server_address, AutoscalerAPIHandler)
    logger.info(f"API Server running on {bind_addr}:{API_PORT}")
    httpd.serve_forever()


# =============================================================================
# Main Loop
# =============================================================================

START_TIME = time.time()

def run_once():
    """Single autoscaler iteration."""
    # Prevent concurrent runs (e.g. main loop vs API trigger)
    if not RUN_LOCK.acquire(blocking=False):
        logger.warning("Autoscaler cycle already in progress — skipping")
        return

    try:
        _run_once_implementation()
    finally:
        RUN_LOCK.release()


def _run_once_implementation():
    stats = get_docker_stats()
    if not stats:
        logger.warning("No container stats available — skipping cycle")
        return

    # Filter to managed containers only
    managed = {k: v for k, v in stats.items() if k in SERVICE_GROUPS}
    if not managed:
        logger.debug("No managed containers running — skipping cycle")
        # Still record history even if empty

    # Calculate demand and make decisions
    demand_scores = calculate_demand_scores(stats)
    decisions = make_scaling_decisions(stats, demand_scores)

    # Log summary
    active_decisions = [d for d in decisions if d.action != 'none']
    if active_decisions:
        logger.info(f"Scaling {len(active_decisions)} containers this cycle")
        apply_decisions(decisions)
    else:
        logger.debug("All containers balanced — no changes needed")

    # Update history and state
    now_iso = datetime.now(UTC).isoformat()
    state_snapshot = {
        'timestamp': now_iso,
        'stats': stats,
        'demand_scores': demand_scores,
        'decisions': decisions
    }

    with HISTORY_LOCK:
        HISTORY.append(state_snapshot)
        global LATEST_STATE
        LATEST_STATE = state_snapshot


def main():
    """Main loop — runs until killed."""
    logger.info(f"SMSLY Autoscaler started (budget={APP_BUDGET_MB}MB, interval={CHECK_INTERVAL}s)")

    # Start API Server in background thread
    api_thread = threading.Thread(target=run_api_server, daemon=True)
    api_thread.start()

    # Handle graceful shutdown
    def shutdown(signum, frame):
        logger.info("Shutting down autoscaler...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            run_once()
        except Exception as e:
            logger.error(f"Autoscaler cycle failed: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == '__main__':
    main()
