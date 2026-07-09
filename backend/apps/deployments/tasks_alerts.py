import logging

logger = logging.getLogger(__name__)
import asyncio
import logging
from typing import Any

import docker
import requests
from celery import shared_task
from decouple import config
from django.core.cache import cache
from django.core.mail import send_mail

from apps.deployments.models import (  # type: ignore[attr-defined]
    Deployment,
)


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


def _create_in_app_notification(owner, title: str, message: str, event_type: str) -> dict[str, Any]:
    try:
        from apps.notifications.models import Notification

        Notification.objects.create(
            user=owner,
            title=title,
            message=message,
            event_type=event_type,
        )
        return {"ok": True}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("In-app notification failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _send_sms_alert(owner, service, message: str, env_map: dict[str, str]) -> dict[str, Any]:
    from services.smsly_client import smsly_client

    alert_phone = _first_non_empty(
        env_map.get("ALERT_PHONE"),
        getattr(owner, "phone_number", ""),
        getattr(owner, "phone", ""),
        config("ALERT_PHONE_NUMBER", default=""),
    )

    if not alert_phone:
        return {"ok": False, "error": "No SMS target configured"}

    result = smsly_client.send_sms_sync(
        to_phone=alert_phone,
        message=message,
        sender_id="SMSLYHost",
    )
    ok = result.get("status") != "failed" and not result.get("error")
    return {"ok": ok, "result": result}


def _send_email_alert(owner, subject: str, message: str, env_map: dict[str, str]) -> dict[str, Any]:
    """
    Send email via Resend if configured, otherwise fall back to Django's send_mail.
    """
    to_email = _first_non_empty(
        env_map.get("ALERT_EMAIL"),
        getattr(owner, "email", ""),
        config("ALERT_EMAIL", default=""),
    )
    if not to_email:
        return {"ok": False, "error": "No email target configured"}

    # Preferred path: Resend
    resend_key = config("RESEND_API_KEY", default="")
    resend_from = config("RESEND_FROM_EMAIL", default=config("DEFAULT_FROM_EMAIL", default="noreply@smsly.cloud"))
    if resend_key:
        try:
            resp = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": resend_from,
                    "to": [to_email],
                    "subject": subject,
                    "text": message,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return {"ok": True, "to": to_email, "provider": "resend"}
        except requests.RequestException as exc:
            logger.warning("Resend email failed, falling back to SMTP: %s", exc)

    # Fallback path: SMTP/Django
    from_email = resend_from
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[to_email],
            fail_silently=False,
        )
        return {"ok": True, "to": to_email, "provider": "smtp"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Email alert failed (SMTP): %s", exc)
        return {"ok": False, "error": str(exc)}


def _send_telegram_alert(message: str, env_map: dict[str, str]) -> dict[str, Any]:
    bot_token = _first_non_empty(
        env_map.get("ALERT_TELEGRAM_BOT_TOKEN"),
        config("TELEGRAM_BOT_TOKEN", default=""),
    )
    chat_id = _first_non_empty(
        env_map.get("ALERT_TELEGRAM_CHAT_ID"),
        config("TELEGRAM_CHAT_ID", default=""),
    )
    if not bot_token or not chat_id:
        return {"ok": False, "error": "Telegram token/chat ID not configured"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": message},
            timeout=15,
        )
        response.raise_for_status()
        return {"ok": True, "chat_id": chat_id}
    except requests.RequestException as exc:
        logger.warning("Telegram alert failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _send_whatsapp_alert(message: str, env_map: dict[str, str]) -> dict[str, Any]:
    # Preferred path: Twilio WhatsApp API
    account_sid = _first_non_empty(
        env_map.get("TWILIO_ACCOUNT_SID"),
        config("TWILIO_ACCOUNT_SID", default=""),
    )
    auth_token = _first_non_empty(
        env_map.get("TWILIO_AUTH_TOKEN"),
        config("TWILIO_AUTH_TOKEN", default=""),
    )
    from_whatsapp = _first_non_empty(
        env_map.get("TWILIO_WHATSAPP_FROM"),
        config("TWILIO_WHATSAPP_FROM", default=""),
    )
    to_whatsapp = _first_non_empty(
        env_map.get("ALERT_WHATSAPP_TO"),
        config("WHATSAPP_ALERT_TO", default=""),
    )

    if account_sid and auth_token and from_whatsapp and to_whatsapp:
        from_value = from_whatsapp if from_whatsapp.startswith("whatsapp:") else f"whatsapp:{from_whatsapp}"
        to_value = to_whatsapp if to_whatsapp.startswith("whatsapp:") else f"whatsapp:{to_whatsapp}"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        try:
            response = requests.post(
                url,
                auth=(account_sid, auth_token),
                data={"From": from_value, "To": to_value, "Body": message},
                timeout=15,
            )
            response.raise_for_status()
            return {"ok": True, "to": to_value}
        except requests.RequestException as exc:
            logger.warning("Twilio WhatsApp alert failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    # Fallback path: generic webhook
    webhook_url = _first_non_empty(
        env_map.get("WHATSAPP_ALERT_WEBHOOK_URL"),
        config("WHATSAPP_ALERT_WEBHOOK_URL", default=""),
    )
    if not webhook_url:
        return {"ok": False, "error": "WhatsApp channel not configured"}

    payload = {"message": message}
    if to_whatsapp:
        payload["to"] = to_whatsapp

    try:
        response = requests.post(webhook_url, json=payload, timeout=15)
        response.raise_for_status()
        return {"ok": True}
    except requests.RequestException as exc:
        logger.warning("WhatsApp webhook alert failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _dispatch_failure_alert(deployment, error_message: str) -> dict[str, Any]:
    service = deployment.service
    owner = service.owner
    env_map = _load_service_env(service)

    if not _service_flag(env_map, "JULES_RUNTIME_WATCH", default=True):
        return {"status": "skipped", "reason": "runtime_watch_disabled"}

    dashboard_url = _get_dashboard_url()
    service_name = service.name
    title = f"Deployment failed: {service_name}"
    subject = f"[SMSLY Hosting] {title}"
    message = (
        f"SMSLY Hosting Alert\n"
        f"Service: {service_name}\n"
        f"Status: FAILED\n"
        f"Error: {error_message[:240]}\n"
        f"View logs: {dashboard_url}/deployments/{deployment.id}"
    )

    channel_results: dict[str, Any] = {}

    if _service_flag(env_map, "JULES_NOTIFY_IN_APP", default=True):
        channel_results["in_app"] = _create_in_app_notification(
            owner,
            title=title,
            message=message,
            event_type="deploy_failed",
        )

    if _service_flag(env_map, "JULES_NOTIFY_SMS", default=True):
        channel_results["sms"] = _send_sms_alert(owner, service, message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_EMAIL", default=True):
        channel_results["email"] = _send_email_alert(owner, subject, message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_TELEGRAM", default=False):
        channel_results["telegram"] = _send_telegram_alert(message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_WHATSAPP", default=False):
        channel_results["whatsapp"] = _send_whatsapp_alert(message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_SLACK", default=False):
        channel_results["slack"] = _send_slack_alert(message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_DISCORD", default=False):
        channel_results["discord"] = _send_discord_alert(message, env_map)

    delivered = [name for name, result in channel_results.items() if result.get("ok")]
    failed = [name for name, result in channel_results.items() if not result.get("ok")]

    return {
        "status": "ok" if delivered else "partial",
        "delivered_channels": delivered,
        "failed_channels": failed,
        "channels": channel_results,
    }


@shared_task
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


@shared_task(bind=True, max_retries=3)
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


@shared_task(bind=True, max_retries=2)
def voice_alert_critical_task(self, deployment_id: str, error_message: str):
    """
    Sends a voice call alert for critical failures.
    """
    from services.smsly_client import smsly_client

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

@shared_task
def notify_deployment_success(deployment_id: str):
    """
    Optional success notification via SMS.
    """
    from services.smsly_client import smsly_client

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


@shared_task
def notify_auto_rollback(service_id: str, trigger: str, reason: str, target_commit: str):
    """
    Notification fired by the centralized auto-rollback engine.

    Dispatches to the same channels as ``_dispatch_failure_alert`` and
    respects the per-service ``JULES_NOTIFY_*`` opt-in flags. Email
    uses the Resend-aware ``_send_email_alert`` helper so operators
    who configured ``RESEND_API_KEY`` get branded transactional email.
    """
    from apps.deployments.models import Service as ServiceModel

    try:
        service = ServiceModel.objects.get(id=service_id)
    except ServiceModel.DoesNotExist:
        logger.warning("notify_auto_rollback: Service %s not found", service_id)
        return {"status": "error", "reason": "service_not_found"}

    env_map = _load_service_env(service)
    owner = getattr(service, "owner", None)
    commit_short = target_commit[:7] if target_commit else "unknown"

    title = f"Auto-Rollback ({trigger}): {service.name}"
    subject = f"[SMSLY Hosting] {title}"
    message = (
        f"⚠️ SMSLY Hosting AUTO-ROLLBACK\n"
        f"Service: {service.name}\n"
        f"Trigger: {trigger}\n"
        f"Reason: {reason}\n"
        f"Reverting to commit: {commit_short}"
    )

    channel_results: dict[str, Any] = {}

    if _service_flag(env_map, "JULES_NOTIFY_IN_APP", default=True):
        channel_results["in_app"] = _create_in_app_notification(
            owner,
            title=title,
            message=message,
            event_type="auto_rollback",
        )

    if _service_flag(env_map, "JULES_NOTIFY_SMS", default=True):
        channel_results["sms"] = _send_sms_alert(owner, service, message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_EMAIL", default=True):
        channel_results["email"] = _send_email_alert(owner, subject, message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_SLACK", default=False):
        channel_results["slack"] = _send_slack_alert(message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_DISCORD", default=False):
        channel_results["discord"] = _send_discord_alert(message, env_map)

    delivered = [name for name, result in channel_results.items() if result.get("ok")]
    failed = [name for name, result in channel_results.items() if result.get("ok") is False]

    logger.info(
        "Auto-rollback notification for %s: delivered=%s failed=%s",
        service.name, delivered, failed,
    )
    return {
        "status": "ok" if delivered else ("partial" if failed else "no_channels_configured"),
        "delivered_channels": delivered,
        "failed_channels": failed,
        "channels": channel_results,
    }


def _send_slack_alert(message, env_map):
    """Send alert to Slack webhook."""
    try:
        webhook = env_map.get("JULES_SLACK_WEBHOOK", "").strip()
        if not webhook:
            return {"ok": False, "reason": "No webhook URL"}
        resp = requests.post(webhook, json={"text": message}, timeout=10)
        return {"ok": resp.ok, "status": resp.status_code}
    except Exception as exc:
        logger.warning("Slack alert failed: %s", exc)
        return {"ok": False, "reason": str(exc)}


def _send_discord_alert(message, env_map):
    """Send alert to Discord webhook."""
    try:
        webhook = env_map.get("JULES_DISCORD_WEBHOOK", "").strip()
        if not webhook:
            return {"ok": False, "reason": "No webhook URL"}
        resp = requests.post(webhook, json={"content": message[:2000]}, timeout=10)
        return {"ok": resp.ok, "status": resp.status_code}
    except Exception as exc:
        logger.warning("Discord alert failed: %s", exc)
        return {"ok": False, "reason": str(exc)}


@shared_task
def _send_alerts_for_backup_cloud_failure(service_id: str, backup_id: str, reason: str, bucket: str, key: str):
    """Dispatch alerts when a backup succeeded locally but cloud upload failed.

    Sends in-app notification, email, and optional SMS/chat channels.
    Cloud failure is non-fatal but the operator needs visibility.
    """
    from apps.deployments.models import Service

    try:
        svc = Service.objects.select_related('owner').only('name', 'owner').get(id=service_id)
    except Service.DoesNotExist:
        logger.warning("Service %s not found for cloud backup failure alert", service_id)
        return {"status": "skipped", "reason": "service_not_found"}

    owner = svc.owner
    env_map = _load_service_env(svc)
    dashboard_url = _get_dashboard_url()

    title = f"Cloud backup upload failed for {svc.name}"
    subject = f"[SMSLY Hosting] {title}"
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

    channel_results: dict[str, Any] = {}

    if _service_flag(env_map, "JULES_NOTIFY_IN_APP", default=True):
        channel_results["in_app"] = _create_in_app_notification(
            owner, title=title, message=message,
            event_type="backup_cloud_failed",
        )

    if _service_flag(env_map, "JULES_NOTIFY_EMAIL", default=True):
        channel_results["email"] = _send_email_alert(owner, subject, message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_SMS", default=True):
        channel_results["sms"] = _send_sms_alert(owner, svc, message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_SLACK", default=False):
        channel_results["slack"] = _send_slack_alert(message, env_map)

    if _service_flag(env_map, "JULES_NOTIFY_DISCORD", default=False):
        channel_results["discord"] = _send_discord_alert(message, env_map)

    delivered = [name for name, r in channel_results.items() if r.get("ok")]
    return {
        "status": "ok" if delivered else "no_channels",
        "delivered_channels": delivered,
        "channels": channel_results,
    }
