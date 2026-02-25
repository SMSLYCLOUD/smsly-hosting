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

import subprocess
import json
import time
import os
import signal
import sys
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

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


# =============================================================================
# Docker Stats Collection
# =============================================================================

def get_docker_stats() -> Dict[str, ContainerStats]:
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

def calculate_demand_scores(stats: Dict[str, ContainerStats]) -> Dict[str, float]:
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
    stats: Dict[str, ContainerStats],
    demand_scores: Dict[str, float]
) -> List[ScalingDecision]:
    """
    Decide how to redistribute resources based on demand.

    Strategy:
    - Total worker budget = APP_BUDGET_MB / WORKER_MEMORY_MB
    - Distribute workers proportional to demand scores
    - Ensure min/max constraints
    - Scale memory limits with worker count
    """
    decisions = []

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
                reason=f'demand={demand:.2f}'
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
            reason=f'demand={demand:.2f}, cpu={s.cpu_percent:.1f}%, mem={s.memory_percent:.1f}%'
        ))

    return decisions


# =============================================================================
# Scaling Actions
# =============================================================================

def apply_decisions(decisions: List[ScalingDecision]):
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
# Main Loop
# =============================================================================

def run_once():
    """Single autoscaler iteration."""
    stats = get_docker_stats()
    if not stats:
        logger.warning("No container stats available — skipping cycle")
        return

    # Filter to managed containers only
    managed = {k: v for k, v in stats.items() if k in SERVICE_GROUPS}
    if not managed:
        logger.debug("No managed containers running — skipping cycle")
        return

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


def main():
    """Main loop — runs until killed."""
    logger.info(f"SMSLY Autoscaler started (budget={APP_BUDGET_MB}MB, interval={CHECK_INTERVAL}s)")

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
