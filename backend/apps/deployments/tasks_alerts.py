"""
Multi-channel alerting for deployment events.

Supports service-level opt-in controls via environment variables:
  - JULES_RUNTIME_WATCH=true|false
  - JULES_NOTIFY_IN_APP=true|false
  - JULES_NOTIFY_SMS=true|false
  - JULES_NOTIFY_EMAIL=true|false
  - JULES_NOTIFY_TELEGRAM=true|false
  - JULES_NOTIFY_WHATSAPP=true|false

Optional channel targets (service env var takes precedence over global env):
  - ALERT_EMAIL
  - ALERT_TELEGRAM_CHAT_ID
  - ALERT_WHATSAPP_TO
  - ALERT_PHONE
"""

import asyncio
import logging
from typing import Any, Dict

import requests
from celery import shared_task
from decouple import config
from django.core.mail import send_mail

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


def _load_service_env(service) -> Dict[str, str]:
    return {
        str(env.key or "").strip().upper(): str(env.value or "").strip()
        for env in service.env_vars.all()
        if str(env.key or "").strip()
    }


def _service_flag(env_map: Dict[str, str], key: str, default: bool) -> bool:
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


def _create_in_app_notification(owner, title: str, message: str, event_type: str) -> Dict[str, Any]:
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


def _send_sms_alert(owner, service, message: str, env_map: Dict[str, str]) -> Dict[str, Any]:
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


def _send_email_alert(owner, subject: str, message: str, env_map: Dict[str, str]) -> Dict[str, Any]:
    to_email = _first_non_empty(
        env_map.get("ALERT_EMAIL"),
        getattr(owner, "email", ""),
        config("ALERT_EMAIL", default=""),
    )
    if not to_email:
        return {"ok": False, "error": "No email target configured"}

    from_email = config("DEFAULT_FROM_EMAIL", default="noreply@smsly.cloud")
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[to_email],
            fail_silently=False,
        )
        return {"ok": True, "to": to_email}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Email alert failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _send_telegram_alert(message: str, env_map: Dict[str, str]) -> Dict[str, Any]:
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


def _send_whatsapp_alert(message: str, env_map: Dict[str, str]) -> Dict[str, Any]:
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


def _dispatch_failure_alert(deployment, error_message: str) -> Dict[str, Any]:
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

    channel_results: Dict[str, Any] = {}

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

    delivered = [name for name, result in channel_results.items() if result.get("ok")]
    failed = [name for name, result in channel_results.items() if not result.get("ok")]

    return {
        "status": "ok" if delivered else "partial",
        "delivered_channels": delivered,
        "failed_channels": failed,
        "channels": channel_results,
    }


@shared_task(bind=True, max_retries=3)
def alert_user_task(self, deployment_id: str, error_message: str):
    """
    Fan out deployment failure notifications across configured channels.
    """
    from apps.deployments.models import Deployment

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
    from apps.deployments.models import Deployment
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

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                smsly_client.send_voice_alert(
                    to_phone=alert_phone,
                    message=message,
                )
            )
        finally:
            loop.close()

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
    from apps.deployments.models import Deployment
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
