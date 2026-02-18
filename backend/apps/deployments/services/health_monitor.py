"""
Health monitor service — monitors container health and auto-restarts unhealthy services.

Checks each service's health_check_path via HTTP request to the container.
Updates health_status and triggers restart if auto_restart is enabled.

Rate limiting:
  - Cooldown period: 10 minutes between auto-restarts per service
  - Max 3 auto-restarts before giving up (requires manual intervention)
  - Exponential backoff: 10min, 20min, 40min between restarts
"""
import logging
import time
import requests
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Track consecutive failures per service (in-memory, resets on worker restart)
_failure_counts = {}

# Track restart state per service: {service_id: {"count": N, "last_restart": timestamp}}
_restart_state = {}

# Config
RESTART_COOLDOWN_BASE = 900       # 15 minutes base cooldown
MAX_AUTO_RESTARTS = 5             # Give up after 5 restarts (needs manual fix)
BACKOFF_MULTIPLIER = 2.5          # Exponential backoff: 15m, 37m, 93m, 234m, 585m


@shared_task
def monitor_health_task():
    """
    Check health for all active services.

    For each service with a health_check_path, sends an HTTP request
    to the container and updates health_status accordingly.
    """
    from apps.deployments.models import Service, Deployment

    services = Service.objects.exclude(health_check_path='')
    checked = 0

    for service in services:
        try:
            _check_service_health(service, Deployment)
            checked += 1
        except Exception as e:
            logger.error("Health check failed for %s: %s", service.name, e)

    logger.debug("Health checked %d services", checked)


def _check_service_health(service, Deployment):
    """Check a single service's health via HTTP."""
    # Find active deployment
    latest = Deployment.objects.filter(
        service=service, status=Deployment.Status.ACTIVE
    ).order_by('-created_at').first()

    if not latest:
        if service.health_status != 'unknown':
            service.health_status = 'unknown'
            service.save(update_fields=['health_status', 'updated_at'])
        return

    # Build health check URL
    # Try container_id first (for direct Docker), then domain
    container_id = latest.container_id
    port = service.internal_port or 8000
    path = service.health_check_path

    # Try health check via public domain first, then container
    health_url = None
    if service.public_domain:
        health_url = f"https://{service.public_domain}{path}"
    elif container_id:
        # Direct container check via Docker bridge network
        health_url = f"http://{container_id[:12]}:{port}{path}"

    if not health_url:
        return

    service_key = str(service.id)
    timeout = service.health_check_timeout or 5

    try:
        resp = requests.get(health_url, timeout=timeout, verify=False)
        is_healthy = 200 <= resp.status_code < 400

        if is_healthy:
            _failure_counts[service_key] = 0
            # Reset restart state on recovery
            if service_key in _restart_state:
                logger.info(
                    "✓ %s recovered — clearing restart state (was %d restarts)",
                    service.name, _restart_state[service_key]["count"]
                )
                del _restart_state[service_key]

            if service.health_status != 'healthy':
                service.health_status = 'healthy'
                service.save(update_fields=['health_status', 'updated_at'])
                logger.info("✓ %s is healthy", service.name)
        else:
            _handle_failure(service, service_key, f"HTTP {resp.status_code}")
    except requests.Timeout:
        _handle_failure(service, service_key, "Timeout")
    except requests.ConnectionError:
        _handle_failure(service, service_key, "Connection refused")
    except Exception as e:
        _handle_failure(service, service_key, str(e))


def _handle_failure(service, service_key, reason):
    """Handle a health check failure. Auto-restart with rate limiting."""
    _failure_counts[service_key] = _failure_counts.get(service_key, 0) + 1
    count = _failure_counts[service_key]
    retries = service.health_check_retries or 3

    logger.warning(
        "✗ %s health check failed (%d/%d): %s",
        service.name, count, retries, reason,
    )

    if count >= retries:
        service.health_status = 'unhealthy'
        service.save(update_fields=['health_status', 'updated_at'])

        if service.auto_restart:
            if _should_restart(service, service_key):
                logger.info("🔄 Auto-restarting unhealthy service: %s", service.name)
                _trigger_restart(service)
            else:
                logger.info(
                    "⏸️ Skipping auto-restart for %s (cooldown or max restarts reached)",
                    service.name
                )
            _failure_counts[service_key] = 0


def _should_restart(service, service_key):
    """
    Rate-limit auto-restarts with exponential backoff and max restart cap.

    Returns True if a restart is allowed, False if in cooldown or exhausted.
    """
    now = time.monotonic()
    state = _restart_state.get(service_key)

    if state is None:
        # First restart — always allowed
        return True

    restart_count = state["count"]
    last_restart = state["last_restart"]

    # Max restarts exhausted — give up
    if restart_count >= MAX_AUTO_RESTARTS:
        logger.warning(
            "🛑 %s has been auto-restarted %d times — giving up. "
            "Manual intervention required.",
            service.name, restart_count
        )
        return False

    # Exponential backoff: 10min, 20min, 40min
    cooldown = RESTART_COOLDOWN_BASE * (BACKOFF_MULTIPLIER ** (restart_count - 1))
    elapsed = now - last_restart

    if elapsed < cooldown:
        remaining = int(cooldown - elapsed)
        logger.info(
            "⏳ %s restart cooldown: %ds remaining (restart %d/%d)",
            service.name, remaining, restart_count, MAX_AUTO_RESTARTS
        )
        return False

    return True


def _trigger_restart(service):
    """Trigger a redeployment of an unhealthy service (with rate tracking)."""
    service_key = str(service.id)

    try:
        from apps.deployments.tasks import smart_deploy_task
        from apps.deployments.models import Deployment

        latest = Deployment.objects.filter(
            service=service, status=Deployment.Status.ACTIVE
        ).order_by('-created_at').first()

        if latest:
            # Create a new deployment that rebuilds from the same commit
            new_deployment = Deployment.objects.create(
                service=service,
                commit_hash=latest.commit_hash or 'HEAD',
                status=Deployment.Status.QUEUED,
            )
            service.health_status = 'starting'
            service.save(update_fields=['health_status', 'updated_at'])

            smart_deploy_task.delay(
                str(service.id),
                str(new_deployment.id),
            )

            # Track restart state
            state = _restart_state.get(service_key, {"count": 0, "last_restart": 0})
            state["count"] += 1
            state["last_restart"] = time.monotonic()
            _restart_state[service_key] = state

            logger.info(
                "Auto-restart triggered for %s (attempt %d/%d)",
                service.name, state["count"], MAX_AUTO_RESTARTS
            )
    except Exception as e:
        logger.error("Auto-restart failed for %s: %s", service.name, e)


def reset_restart_state(service_id: str):
    """
    Clear the restart state for a service.
    Call this when a user manually deploys or restarts a service.
    """
    service_key = str(service_id)
    if service_key in _restart_state:
        del _restart_state[service_key]
    if service_key in _failure_counts:
        del _failure_counts[service_key]
    logger.info("Restart state cleared for service %s", service_id)
