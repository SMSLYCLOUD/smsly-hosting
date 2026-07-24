import asyncio
import logging
from typing import Any

import docker
from celery import shared_task
from decouple import config
from django.core.cache import cache

from apps.deployments.models import (  # type: ignore[attr-defined]
    Deployment,
)

logger = logging.getLogger(__name__)


def _env_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if not text:
        return default
    return text in ("1", "true", "yes", "on", "enabled")


def _load_service_env(service) -> dict[str, str]:
    return {
        str(env.key or "").strip().upper(): str(env.value or "").strip()
        for env in service.env_vars.all()
        if str(env.key or "").strip()
    }


def _service_flag(env_map: dict[str, str], key: str, default: bool) -> bool:
    return _env_bool(env_map.get(key.upper()), default=default)


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _get_dashboard_url() -> str:
    scheme = "https" if config("USE_SSL", default=False, cast=bool) else "http"
    host = config("DOMAIN", default="cloud.smsly.cloud")
    return f"{scheme}://{host}"


def _dispatch_notification(
    owner,
    title: str,
    message: str,
    event_type: str,
    env_map: dict[str, str],
    channel_flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    from apps.notifications.tasks import dispatch_notification

    if channel_flags is None:
        channel_flags = {
            "in_app": _service_flag(env_map, "JULES_NOTIFY_IN_APP", default=True),
            "sms": _service_flag(env_map, "JULES_NOTIFY_SMS", default=True),
            "email": _service_flag(env_map, "JULES_NOTIFY_EMAIL", default=True),
            "webhook": (_service_flag(env_map, "JULES_NOTIFY_SLACK", default=False)
                        or _service_flag(env_map, "JULES_NOTIFY_DISCORD", default=False)),
            "telegram": _service_flag(env_map, "JULES_NOTIFY_TELEGRAM", default=False),
            "whatsapp": _service_flag(env_map, "JULES_NOTIFY_WHATSAPP", default=False),
        }

    channels = [ch for ch, enabled in channel_flags.items() if enabled]
    if not channels:
        return {"status": "skipped", "reason": "no_channels_enabled"}

    metadata = {
        "telegram_bot_token": env_map.get("ALERT_TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": env_map.get("ALERT_TELEGRAM_CHAT_ID"),
        "twilio_account_sid": env_map.get("TWILIO_ACCOUNT_SID"),
        "twilio_auth_token": env_map.get("TWILIO_AUTH_TOKEN"),
        "twilio_whatsapp_from": env_map.get("TWILIO_WHATSAPP_FROM"),
        "alert_whatsapp_to": env_map.get("ALERT_WHATSAPP_TO"),
        "whatsapp_webhook_url": env_map.get("WHATSAPP_ALERT_WEBHOOK_URL"),
        "slack_webhook_url": env_map.get("JULES_SLACK_WEBHOOK"),
        "discord_webhook_url": env_map.get("JULES_DISCORD_WEBHOOK"),
        "alert_phone": env_map.get("ALERT_PHONE"),
    }

    webhook_url = env_map.get("JULES_SLACK_WEBHOOK") or env_map.get("JULES_DISCORD_WEBHOOK")

    dispatch_notification.delay(
        event_type=event_type,
        user_id=owner.id,
        title=title,
        message=message,
        metadata=metadata,
        channels=channels,
        webhook_url=webhook_url,
    )
    return {"status": "ok", "dispatched_via": "dispatch_notification"}


def _dispatch_failure_alert(deployment, error_message: str) -> dict[str, Any]:
    service = deployment.service
    owner = service.owner
    env_map = _load_service_env(service)

    if not _service_flag(env_map, "JULES_RUNTIME_WATCH", default=True):
        return {"status": "skipped", "reason": "runtime_watch_disabled"}

    dashboard_url = _get_dashboard_url()
    service_name = service.name
    title = f"Deployment failed: {service_name}"
    message = (
        f"SMSLY Hosting Alert\n"
        f"Service: {service_name}\n"
        f"Status: FAILED\n"
        f"Error: {error_message[:240]}\n"
        f"View logs: {dashboard_url}/deployments/{deployment.id}"
    )

    return _dispatch_notification(owner, title, message, "deploy_failed", env_map)


@shared_task(soft_time_limit=300, time_limit=360)
def scan_running_containers_logs_task():
    """
    Periodically scans logs of all active containers for crashing errors.
    If an error is found, dispatches an alert.
    """
    from apps.deployments.models import Deployment
    from apps.deployments.services.error_resolver import diagnose_runtime_logs

    try:
        client = docker.from_env()
    except Exception as exc:
        logger.warning("Docker client unavailable for log scanning: %s", exc)
        return

    active_deployments = Deployment.objects.filter(status='ACTIVE').select_related('service')
    for deployment in active_deployments:
        if not deployment.container_id:
            continue

        try:
            container = client.containers.get(deployment.container_id)
            # Only check if container is running or recently died
            if container.status not in ['running', 'exited', 'dead', 'restarting']:
                continue

            # Fetch logs for the last 5 minutes to avoid alerting on old errors
            # 300 seconds = 5 minutes
            import time
            since_ts = int(time.time()) - 300

            logs_bytes = container.logs(since=since_ts, tail=500)
            logs_str = logs_bytes.decode('utf-8', errors='replace')

            if not logs_str.strip():
                continue

            # Use error resolver to find patterns
            results = diagnose_runtime_logs(logs_str, service=deployment.service, deployment=deployment, auto_apply=False)

            # Check if we have critical errors
            critical_errors = [r for r in results if r.get('severity') == 'critical']

            if critical_errors:
                # Rate limit alerts so we don't spam the user every 5 minutes for the same error
                cache_key = f"alert_sent_crash_{deployment.id}"
                if not cache.get(cache_key):
                    error_msg = critical_errors[0].get('diagnosis', 'Unknown critical error')
                    alert_user_task.delay(str(deployment.id), f"Runtime Crash/Error detected: {error_msg}")
                    # Silence this specific deployment's crash alerts for 1 hour
                    cache.set(cache_key, True, timeout=3600)

        except docker.errors.NotFound:
            pass
        except Exception as exc:
            logger.warning("Error scanning logs for deployment %s: %s", deployment.id, exc)


@shared_task(bind=True, max_retries=3, soft_time_limit=120, time_limit=150)
def alert_user_task(self, deployment_id: str, error_message: str):
    """
    Fan out deployment failure notifications across configured channels.
    """

    try:
        deployment = Deployment.objects.select_related("service", "service__owner").get(id=deployment_id)
        result = _dispatch_failure_alert(deployment, error_message)
        logger.info("Alert fan-out result for %s: %s", deployment_id, result)
        return result

    except Deployment.DoesNotExist:
        logger.error("Deployment %s not found", deployment_id)
        return {"status": "error", "reason": "deployment_not_found"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to dispatch alerts: %s", exc)
        raise self.retry(exc=exc, countdown=30)


@shared_task(bind=True, max_retries=2, soft_time_limit=120, time_limit=150)
def voice_alert_critical_task(self, deployment_id: str, error_message: str):
    """
    Sends a voice call alert for critical failures.
    """
    from apps.core.services.smsly_client import smsly_client

    try:
        deployment = Deployment.objects.select_related('service').get(id=deployment_id)
        service_name = deployment.service.name

        alert_phone = config('CRITICAL_ALERT_PHONE', default='')

        if not alert_phone:
            logger.warning("No CRITICAL_ALERT_PHONE configured. Skipping voice alert.")
            return {"status": "skipped", "reason": "no_phone_configured"}

        message = (
            f"Critical alert from SMSLY Hosting. "
            f"Your service {service_name} has failed deployment. "
            f"Error: {error_message[:50]}. "
            f"Please check your dashboard immediately."
        )

        try:
            result = asyncio.run(
                smsly_client.send_voice_alert(
                    to_phone=alert_phone,
                    message=message,
                )
            )
        except Exception as voice_exc:  # pylint: disable=broad-exception-caught
            logger.exception("send_voice_alert failed: %s", voice_exc)
            raise

        logger.info("Voice alert sent for deployment %s: %s", deployment_id, result)
        return result

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to send voice alert: %s", exc)
        raise self.retry(exc=exc, countdown=60)

@shared_task(bind=True, soft_time_limit=60, time_limit=90)
def notify_deployment_success(self, deployment_id: str):
    """
    Optional success notification via SMS.
    """
    from apps.core.services.smsly_client import smsly_client

    try:
        deployment = Deployment.objects.select_related('service').get(id=deployment_id)
        service_name = deployment.service.name

        notify_success = config('NOTIFY_ON_SUCCESS', default=False, cast=bool)
        if not notify_success:
            return {"status": "skipped", "reason": "success_notifications_disabled"}

        alert_phone = config('ALERT_PHONE_NUMBER', default='')
        if not alert_phone:
            return {"status": "skipped", "reason": "no_phone_configured"}

        message = (
            f"SMSLY Hosting\n"
            f"Service: {service_name}\n"
            f"Status: DEPLOYED\n"
            f"Commit: {deployment.commit_hash[:7]}"
        )

        result = smsly_client.send_sms_sync(
            to_phone=alert_phone,
            message=message,
            sender_id='SMSLYHost',
        )

        return result

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to send success notification: %s", exc)
        return {"status": "error", "reason": str(exc)}


@shared_task(bind=True, soft_time_limit=60, time_limit=90)
def notify_auto_rollback(self, service_id: str, trigger: str, reason: str, target_commit: str):
    from apps.deployments.models import Service as ServiceModel

    try:
        service = ServiceModel.objects.get(id=service_id)
    except ServiceModel.DoesNotExist:
        logger.warning("notify_auto_rollback: Service %s not found", service_id)
        return {"status": "error", "reason": "service_not_found"}

    env_map = _load_service_env(service)
    owner = getattr(service, "owner", None)
    if not owner:
        logger.warning("notify_auto_rollback: Service %s has no owner", service_id)
        return {"status": "skipped", "reason": "no_owner"}

    commit_short = target_commit[:7] if target_commit else "unknown"
    title = f"Auto-Rollback ({trigger}): {service.name}"
    message = (
        f"SMSLY Hosting AUTO-ROLLBACK\n"
        f"Service: {service.name}\n"
        f"Trigger: {trigger}\n"
        f"Reason: {reason}\n"
        f"Reverting to commit: {commit_short}"
    )

    return _dispatch_notification(owner, title, message, "auto_rollback", env_map)


@shared_task(soft_time_limit=60, time_limit=90)
def _send_alerts_for_backup_cloud_failure(service_id: str, backup_id: str, reason: str, bucket: str, key: str):
    from apps.deployments.models import Service

    try:
        svc = Service.objects.select_related('owner').only('name', 'owner').get(id=service_id)
    except Service.DoesNotExist:
        logger.warning("Service %s not found for cloud backup failure alert", service_id)
        return {"status": "skipped", "reason": "service_not_found"}

    owner = svc.owner
    if not owner:
        logger.warning("Service %s has no owner for cloud backup failure alert", service_id)
        return {"status": "skipped", "reason": "no_owner"}

    env_map = _load_service_env(svc)
    dashboard_url = _get_dashboard_url()

    title = f"Cloud backup upload failed for {svc.name}"
    message = (
        f"SMSLY Hosting Alert\n"
        f"Service: {svc.name}\n"
        f"Backup ID: {backup_id}\n"
        f"Status: Local backup completed, cloud upload FAILED\n"
        f"Bucket: {bucket}\n"
        f"Key: {key}\n"
        f"Reason: {reason}\n"
        f"The backup is safe on the local server.\n"
        f"View details: {dashboard_url}/services/{service_id}"
    )

    return _dispatch_notification(owner, title, message, "backup_cloud_failed", env_map)
