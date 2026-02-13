"""
SMS/Voice alerting for deployment events.
Integrates with SMSLY Platform services for real notifications.
"""
import logging
from celery import shared_task
from decouple import config

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def alert_user_task(self, deployment_id: str, error_message: str):
    """
    Sends an SMS alert to the user about a failed deployment.
    Uses the internal SMSLY-SMS microservice.

    Args:
        deployment_id: UUID of the failed deployment
        error_message: Error message to include
    """
    from apps.deployments.models import Deployment
    from services.smsly_client import smsly_client

    try:
        deployment = Deployment.objects.select_related(
            'service').get(id=deployment_id)
        service_name = deployment.service.name

        # TODO: In production, fetch phone number from user profile
        # For now, use configured alert phone
        alert_phone = config('ALERT_PHONE_NUMBER', default='')

        if not alert_phone:
            logger.warning(
                "No ALERT_PHONE_NUMBER configured. Skipping SMS alert.")
            return {"status": "skipped", "reason": "no_phone_configured"}

        scheme = "https" if config("USE_SSL", default=False, cast=bool) else "http"
        dashboard_host = config("DOMAIN", default="cloud.smsly.cloud")
        dashboard_url = f"{scheme}://{dashboard_host}"

        # Compose alert message
        message = (
            f"SMSLY Hosting Alert\n"
            f"Service: {service_name}\n"
            f"Status: FAILED\n"
            f"Error: {error_message[:100]}\n"
            f"View logs: {dashboard_url}/deployments/{deployment_id}"
        )

        # Send via SMSLY SMS service
        result = smsly_client.send_sms_sync(
            to_phone=alert_phone,
            message=message,
            sender_id="SMSLYHost"
        )

        logger.info(f"Alert sent for deployment {deployment_id}: {result}")
        return result

    except Deployment.DoesNotExist:
        logger.error(f"Deployment {deployment_id} not found")
        return {"status": "error", "reason": "deployment_not_found"}
    except Exception as e:
        logger.exception(f"Failed to send alert: {str(e)}")
        # Retry on transient failures
        raise self.retry(exc=e, countdown=30)


@shared_task(bind=True, max_retries=2)
def voice_alert_critical_task(self, deployment_id: str, error_message: str):
    """
    Sends a voice call alert for critical failures.
    Used for P0 incidents that require immediate attention.
    """
    from apps.deployments.models import Deployment
    from services.smsly_client import smsly_client
    import asyncio

    try:
        deployment = Deployment.objects.select_related(
            'service').get(id=deployment_id)
        service_name = deployment.service.name

        alert_phone = config('CRITICAL_ALERT_PHONE', default='')

        if not alert_phone:
            logger.warning(
                "No CRITICAL_ALERT_PHONE configured. Skipping voice alert.")
            return {"status": "skipped", "reason": "no_phone_configured"}

        message = (
            f"Critical alert from SMSLY Hosting. "
            f"Your service {service_name} has failed deployment. "
            f"Error: {error_message[:50]}. "
            f"Please check your dashboard immediately."
        )

        # Run async voice call
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                smsly_client.send_voice_alert(
                    to_phone=alert_phone,
                    message=message
                )
            )
        finally:
            loop.close()

        logger.info(
            f"Voice alert sent for deployment {deployment_id}: {result}")
        return result

    except Exception as e:
        logger.exception(f"Failed to send voice alert: {str(e)}")
        raise self.retry(exc=e, countdown=60)


@shared_task
def notify_deployment_success(deployment_id: str):
    """
    Optional: Send success notification via SMS.
    Can be enabled per-service in settings.
    """
    from apps.deployments.models import Deployment
    from services.smsly_client import smsly_client

    try:
        deployment = Deployment.objects.select_related(
            'service').get(id=deployment_id)
        service_name = deployment.service.name

        # Check if success notifications are enabled
        notify_success = config('NOTIFY_ON_SUCCESS', default=False, cast=bool)
        if not notify_success:
            return {"status": "skipped",
                    "reason": "success_notifications_disabled"}

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
            sender_id="SMSLYHost"
        )

        return result

    except Exception as e:
        logger.exception(f"Failed to send success notification: {str(e)}")
        return {"status": "error", "reason": str(e)}
