"""
SMSLY Hosting — Notification Task Layer
========================================

Celery background tasks for dispatching notifications across all supported channels:
  - email       → Django email backend (SMTP) or SMSLY Platform API
  - sms         → SMSLY SMS microservice (HMAC V2 signed)
  - webhook     → Slack / Discord / generic HTTP webhook
  - in_app      → Creates a Notification record in the database

Architecture:
  - Every channel dispatch is fully audited via log_event()
  - Celery retry with exponential backoff (max 3 retries, 2^n * 30s delay)
  - Fail-closed: missing env vars raise immediately — no silent no-ops
  - Provider errors are captured with full metadata for post-mortem analysis

Usage:
    from apps.notifications.tasks import dispatch_notification
    dispatch_notification.delay(
        event_type='deploy_failed',
        user_id=42,
        title='Deployment Failed',
        message='Service api-gateway failed to deploy.',
        metadata={'service': 'api-gateway', 'commit': 'abc1234'},
    )
"""

import hashlib
import hmac
import html
import json
import logging
import operator
import os
import time

import requests
from celery import shared_task

from apps.deployments.constants import RETRY_DELAY_FAST, TASK_TIME_LIMIT_QUICK, TASK_TIME_LIMIT_STANDARD, TASK_TIME_LIMIT_TRIVIAL
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, send_mail
from django.utils import timezone

from apps.deployments.utils import log_event

logger = logging.getLogger(__name__)
User = get_user_model()

# ── Constants ────────────────────────────────────────────────────────────────

CHANNEL_EMAIL    = 'email'
CHANNEL_SMS      = 'sms'
CHANNEL_WEBHOOK  = 'webhook'
CHANNEL_IN_APP   = 'in_app'
CHANNEL_TELEGRAM = 'telegram'
CHANNEL_WHATSAPP = 'whatsapp'

# Max time to wait for external HTTP calls (per channel)
REQUEST_TIMEOUT = 8  # seconds

# ── HMAC V2 Helper ───────────────────────────────────────────────────────────

def _build_hmac_headers(payload: dict) -> dict:
    """
    Build HMAC V2 signed headers for inter-service communication with the
    SMSLY Platform API. Matches the Gateway Secret scheme used across
    the SMSLY microservices ecosystem.
    """
    gateway_secret = os.environ['GATEWAY_SECRET']  # Fail-closed: crash if missing
    body = json.dumps(payload, sort_keys=True).encode('utf-8')
    timestamp = str(int(time.time()))
    sig_input = f"{timestamp}.{body.decode('utf-8')}"
    signature = hmac.new(
        gateway_secret.encode('utf-8'),
        sig_input.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return {
        'Content-Type': 'application/json',
        'X-SMSLY-Timestamp': timestamp,
        'X-SMSLY-Signature': f'sha256={signature}',
    }


# ── Channel Dispatch Functions ───────────────────────────────────────────────

def _dispatch_email(user, title: str, message: str, metadata: dict) -> dict:
    """
    Send a notification email via Django's email backend.
    Falls back gracefully if email is not configured.
    """
    result = {'channel': CHANNEL_EMAIL, 'status': 'skipped', 'recipient': None}

    if not getattr(user, 'email', None):
        result['reason'] = 'user has no email address'
        return result

    # Skip silently when SMTP isn't configured — the default Django SMTP
    # backend is the in-memory console backend (no real host) on a stock
    # install, so the connection error spams the logs every deploy.
    smtp_host = (
        os.environ.get('SMTP_HOST')
        or getattr(settings, 'EMAIL_HOST', None)
    )
    if not smtp_host:
        result['reason'] = 'SMTP not configured (set SMTP_HOST env var to enable)'
        logger.debug("[notify:email] Skipped for %s — SMTP_HOST unset", user.email)
        return result

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@smsly.cloud')
    recipient = user.email
    result['recipient'] = recipient

    try:
        html_body = (
            f"<html><body>"
            f"<h2 style='font-family:sans-serif'>{html.escape(title)}</h2>"
            f"<p style='font-family:sans-serif;color:#555'>{html.escape(message)}</p>"
            f"<hr/><p style='font-family:sans-serif;font-size:12px;color:#999'>"
            f"Grid by SMSLY | {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}"
            f"</p></body></html>"
        )
        email = EmailMultiAlternatives(
            subject=f"[Grid] {title}",
            body=message,  # plain-text fallback
            from_email=from_email,
            to=[recipient],
        )
        email.attach_alternative(html_body, "text/html")
        email.send(fail_silently=False)

        result['status'] = 'delivered'
        logger.info("[notify:email] Delivered to %s — %s", recipient, title)

    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        logger.error("[notify:email] Failed for %s: %s", recipient, exc)

    return result


def _dispatch_sms(user, message: str, metadata: dict) -> dict:
    """
    Send a notification SMS via the SMSLY SMS microservice.
    Requires SMSLY_SMS_API_URL and GATEWAY_SECRET in the environment.
    """
    result = {'channel': CHANNEL_SMS, 'status': 'skipped', 'recipient': None}

    sms_api_url = os.environ.get('SMSLY_SMS_API_URL', '')
    if not sms_api_url:
        result['reason'] = 'SMSLY_SMS_API_URL not configured'
        return result

    phone = getattr(user, 'phone_number', None) or metadata.get('phone_number')
    if not phone:
        result['reason'] = 'no phone number available for user'
        return result

    result['recipient'] = phone

    payload = {
        'to': phone,
        'message': message[:160],  # Standard SMS length
        'source': 'grid-hosting',
        'event_type': metadata.get('event_type', 'platform_alert'),
        'user_id': str(user.id),
    }

    try:
        headers = _build_hmac_headers(payload)
        response = requests.post(
            f"{sms_api_url.rstrip('/')}/api/v1/send/",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        result['status'] = 'delivered'
        result['provider_response'] = response.json()
        logger.info("[notify:sms] Delivered to %s", phone)

    except requests.HTTPError as exc:
        result['status'] = 'failed'
        result['error'] = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        logger.error("[notify:sms] HTTP error for %s: %s", phone, exc)
    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        logger.error("[notify:sms] Failed for %s: %s", phone, exc)

    return result


def _dispatch_webhook(user, title: str, message: str, metadata: dict, webhook_url: str | None = None) -> dict:
    """
    Send a notification to a configured webhook (Slack, Discord, or generic HTTP).
    Preference order: per-user webhook_url → settings.SLACK_WEBHOOK_URL → settings.DISCORD_WEBHOOK_URL
    """
    from apps.notifications.webhooks import (
        send_discord_notification,
        send_slack_notification,
    )

    result = {'channel': CHANNEL_WEBHOOK, 'status': 'skipped'}

    url = (
        webhook_url
        or getattr(settings, 'SLACK_WEBHOOK_URL', None)
        or getattr(settings, 'DISCORD_WEBHOOK_URL', None)
    )
    if not url:
        result['reason'] = 'no webhook URL configured'
        return result

    text = f"*[Grid]* {title}\n{message}"

    try:
        if 'slack' in url:
            send_slack_notification(text, url)
            result['provider'] = 'slack'
        elif 'discord' in url:
            send_discord_notification(text, url)
            result['provider'] = 'discord'
        else:
            # Generic webhook — POST JSON payload.  Validate the URL
            # against SSRF before making the request.
            from apps.notifications.webhooks import _validate_notification_url
            try:
                _validate_notification_url(url)
            except ValueError as exc:
                raise ValueError(f"Webhook URL rejected: {exc}") from exc

            payload = {
                'title': title,
                'message': message,
                'event_type': metadata.get('event_type', ''),
                'source': 'smsly',
                'timestamp': timezone.now().isoformat(),
            }
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            result['provider'] = 'generic'

        result['status'] = 'delivered'
        logger.info("[notify:webhook] Delivered to %s", url[:40])

    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        logger.error("[notify:webhook] Failed: %s", exc)

    return result


def _dispatch_telegram(user, message: str, metadata: dict) -> dict:
    bot_token = metadata.get('telegram_bot_token') or os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat_id = metadata.get('telegram_chat_id') or os.environ.get('TELEGRAM_CHAT_ID', '')
    if not bot_token or not chat_id:
        return {'channel': CHANNEL_TELEGRAM, 'status': 'skipped', 'reason': 'Telegram token or chat ID not configured'}
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, json={'chat_id': chat_id, 'text': message}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return {'channel': CHANNEL_TELEGRAM, 'status': 'delivered', 'chat_id': chat_id}
    except requests.RequestException as exc:
        return {'channel': CHANNEL_TELEGRAM, 'status': 'failed', 'error': str(exc)}


def _dispatch_whatsapp(user, message: str, metadata: dict) -> dict:
    account_sid = metadata.get('twilio_account_sid') or os.environ.get('TWILIO_ACCOUNT_SID', '')
    auth_token = metadata.get('twilio_auth_token') or os.environ.get('TWILIO_AUTH_TOKEN', '')
    from_whatsapp = metadata.get('twilio_whatsapp_from') or os.environ.get('TWILIO_WHATSAPP_FROM', '')
    to_whatsapp = metadata.get('alert_whatsapp_to') or os.environ.get('WHATSAPP_ALERT_TO', '')
    if account_sid and auth_token and from_whatsapp and to_whatsapp:
        from_val = from_whatsapp if from_whatsapp.startswith('whatsapp:') else f'whatsapp:{from_whatsapp}'
        to_val = to_whatsapp if to_whatsapp.startswith('whatsapp:') else f'whatsapp:{to_whatsapp}'
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        try:
            resp = requests.post(url, auth=(account_sid, auth_token), data={'From': from_val, 'To': to_val, 'Body': message}, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return {'channel': CHANNEL_WHATSAPP, 'status': 'delivered', 'to': to_val}
        except requests.RequestException as exc:
            return {'channel': CHANNEL_WHATSAPP, 'status': 'failed', 'error': str(exc)}
    webhook_url = metadata.get('whatsapp_webhook_url') or os.environ.get('WHATSAPP_ALERT_WEBHOOK_URL', '')
    if not webhook_url:
        return {'channel': CHANNEL_WHATSAPP, 'status': 'skipped', 'reason': 'WhatsApp channel not configured'}
    try:
        payload = {'message': message}
        if to_whatsapp:
            payload['to'] = to_whatsapp
        resp = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return {'channel': CHANNEL_WHATSAPP, 'status': 'delivered'}
    except requests.RequestException as exc:
        return {'channel': CHANNEL_WHATSAPP, 'status': 'failed', 'error': str(exc)}


def _dispatch_in_app(user, title: str, message: str, event_type: str) -> dict:
    """
    Create an in-app Notification record for display in the platform dashboard.
    This is always attempted regardless of other channel states.
    """
    from apps.notifications.models import Notification

    result = {'channel': CHANNEL_IN_APP, 'status': 'skipped'}

    try:
        Notification.objects.create(
            user=user,
            title=title,
            message=message,
            event_type=event_type,
        )
        result['status'] = 'delivered'
        logger.info("[notify:in_app] Created for user %s — %s", user.id, event_type)

    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        logger.error("[notify:in_app] Failed for user %s: %s", user.id, exc)

    return result


# ── Core Dispatch Task ───────────────────────────────────────────────────────

@shared_task(
    bind=True,
    name='notifications.dispatch_notification',
    queue='fast',
    max_retries=3,
    default_retry_delay=RETRY_DELAY_FAST,
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    acks_late=True,
)
def dispatch_notification(
    self,
    event_type: str,
    user_id: int,
    title: str,
    message: str,
    metadata: dict | None = None,
    channels: list | None = None,
    webhook_url: str | None = None,
):
    """
    Master notification dispatcher.

    Resolves the target user's channel preferences, then dispatches the
    notification across all configured channels in parallel-safe sequence.
    Every result is recorded via log_event() for full observability.

    Args:
        event_type:  Platform event identifier (e.g. 'deploy_failed')
        user_id:     Target user's primary key
        title:       Short notification headline
        message:     Full notification body
        metadata:    Contextual info (service name, commit hash, etc.)
        channels:    Override channel list. If None, reads from NotificationPreference
        webhook_url: Optional per-call webhook override
    """
    metadata = metadata or {}
    metadata['event_type'] = event_type
    started_at = timezone.now().isoformat()

    # ── Resolve user ──────────────────────────────────────────────────────
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.error("[notify] User %s not found — aborting dispatch", user_id)
        log_event(
            action='notification.dispatch.user_not_found',
            target=f'user:{user_id}',
            actor='system',
            metadata={**metadata, 'event_type': event_type, 'title': title},
        )
        return {'error': 'user_not_found', 'user_id': user_id}

    # ── Resolve channels ──────────────────────────────────────────────────
    if channels is None:
        from apps.notifications.models import NotificationPreference
        try:
            pref = NotificationPreference.objects.get(user=user, event_type=event_type)
            channels = pref.channels or [CHANNEL_IN_APP]
        except NotificationPreference.DoesNotExist:
            # Default: in_app always + email if user has an address
            channels = [CHANNEL_IN_APP]
            if getattr(user, 'email', None):
                channels.append(CHANNEL_EMAIL)

    log_event(
        action='notification.dispatch.started',
        target=f'user:{user_id}',
        actor='system',
        metadata={
            **metadata,
            'title': title,
            'channels': channels,
            'started_at': started_at,
        },
    )

    # ── Dispatch per channel ──────────────────────────────────────────────
    results = []
    had_failure = False

    for channel in channels:
        try:
            if channel == CHANNEL_IN_APP:
                result = _dispatch_in_app(user, title, message, event_type)
            elif channel == CHANNEL_EMAIL:
                result = _dispatch_email(user, title, message, metadata)
            elif channel == CHANNEL_SMS:
                result = _dispatch_sms(user, message, metadata)
            elif channel == CHANNEL_WEBHOOK:
                result = _dispatch_webhook(user, title, message, metadata, webhook_url)
            elif channel == CHANNEL_TELEGRAM:
                result = _dispatch_telegram(user, message, metadata)
            elif channel == CHANNEL_WHATSAPP:
                result = _dispatch_whatsapp(user, message, metadata)
            else:
                result = {'channel': channel, 'status': 'unknown_channel'}

            results.append(result)

            if result.get('status') == 'failed':
                had_failure = True

            log_event(
                action=f'notification.channel.{result.get("status", "unknown")}',
                target=f'user:{user_id}',
                actor='system',
                metadata={
                    **metadata,
                    'channel': channel,
                    'result': result,
                    'event_type': event_type,
                },
            )

        except Exception as exc:
            had_failure = True
            results.append({'channel': channel, 'status': 'exception', 'error': str(exc)})
            logger.exception("[notify] Unexpected error on channel %s for user %s", channel, user_id)

    # ── Summary audit ─────────────────────────────────────────────────────
    delivered = [r for r in results if r.get('status') == 'delivered']
    log_event(
        action='notification.dispatch.completed',
        target=f'user:{user_id}',
        actor='system',
        metadata={
            **metadata,
            'title': title,
            'channels_attempted': len(results),
            'channels_delivered': len(delivered),
            'had_failure': had_failure,
            'results': results,
        },
    )

    # ── Retry if any channel failed (excluding in_app — never retry DB writes) ──
    failed_channels = [r['channel'] for r in results if r.get('status') == 'failed' and r['channel'] != CHANNEL_IN_APP]
    if failed_channels:
        logger.warning("[notify] Channels failed: %s — scheduling retry %d/%d", failed_channels, self.request.retries, self.max_retries)
        try:
            raise self.retry(
                countdown=30 * (2 ** self.request.retries),  # 30s, 60s, 120s
                kwargs={
                    'event_type': event_type,
                    'user_id': user_id,
                    'title': title,
                    'message': message,
                    'metadata': metadata,
                    'channels': failed_channels,  # Only retry the channels that failed
                    'webhook_url': webhook_url,
                },
            )
        except self.MaxRetriesExceededError:
            logger.error("[notify] Max retries exceeded for user %s, event %s", user_id, event_type)
            log_event(
                action='notification.dispatch.max_retries_exceeded',
                target=f'user:{user_id}',
                actor='system',
                metadata={**metadata, 'failed_channels': failed_channels},
            )

    return {
        'user_id': user_id,
        'event_type': event_type,
        'channels_attempted': len(results),
        'channels_delivered': len(delivered),
        'results': results,
    }


# ── Convenience Signal Wrappers ───────────────────────────────────────────────

@shared_task(name='notifications.notify_deploy_event', queue='fast', soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def notify_deploy_event(user_id: int, service_name: str, status: str, commit_hash: str = '', error: str = ''):
    """Fire a deploy success or failure notification for a service owner."""
    if status == 'success':
        title = f"✅ Deployment Succeeded: {service_name}"
        message = f"Your service '{service_name}' deployed successfully."
        if commit_hash:
            message += f"\nCommit: {commit_hash[:8]}"
        event_type = 'deploy_success'
    else:
        title = f"❌ Deployment Failed: {service_name}"
        message = f"Your service '{service_name}' failed to deploy."
        if error:
            message += f"\nReason: {error[:300]}"
        event_type = 'deploy_failed'

    dispatch_notification.delay(
        event_type=event_type,
        user_id=user_id,
        title=title,
        message=message,
        metadata={
            'service': service_name,
            'commit_hash': commit_hash,
            'deploy_status': status,
            'error': error,
        },
    )


@shared_task(name='notifications.notify_health_alert', queue='fast', soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def notify_health_alert(user_id: int, service_name: str, metric: str, current_value: float, threshold: float, severity: str = 'WARNING', message: str = ''):
    """Fire a resource/health alert notification for a service owner."""
    severity_emoji = {'INFO': 'ℹ️', 'WARNING': '⚠️', 'CRITICAL': '🚨'}.get(severity, '⚠️')
    title = f"{severity_emoji} {severity}: {service_name} — {metric.upper()} Alert"
    if message:
        body = message
    else:
        body = (
            f"Service '{service_name}' has triggered a {severity.lower()} alert.\n"
            f"Metric: {metric.upper()}\n"
            f"Current: {current_value:.1f}% | Threshold: {threshold:.1f}%"
        )
    dispatch_notification.delay(
        event_type='health_alert',
        user_id=user_id,
        title=title,
        message=body,
        metadata={
            'service': service_name,
            'metric': metric,
            'current_value': current_value,
            'threshold': threshold,
            'severity': severity,
        },
    )


@shared_task(name='notifications.notify_ssl_expiring', queue='fast', soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def notify_ssl_expiring(user_id: int, domain: str, days_remaining: int):
    """Fire an SSL expiry warning notification."""
    urgency = '🚨 URGENT' if days_remaining <= 7 else '⚠️ Warning'
    dispatch_notification.delay(
        event_type='ssl_expiring',
        user_id=user_id,
        title=f"{urgency}: SSL Certificate Expiring for {domain}",
        message=(
            f"The SSL certificate for '{domain}' expires in {days_remaining} day(s).\n"
            f"Grid will attempt automatic renewal via Let's Encrypt. "
            f"If renewal fails, your site will show security warnings."
        ),
        metadata={'domain': domain, 'days_remaining': days_remaining},
    )


@shared_task(name='notifications.notify_backup_completed', queue='fast', soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def notify_backup_completed(user_id: int, backup_id: str, size_mb: float, success: bool):
    """Fire a backup completion notification."""
    if success:
        title = "✅ Backup Completed"
        message = f"Your platform backup completed successfully.\nBackup ID: {backup_id}\nSize: {size_mb:.1f} MB"
    else:
        title = "❌ Backup Failed"
        message = f"Your platform backup failed.\nBackup ID: {backup_id}\nPlease check the operations runbook."

    dispatch_notification.delay(
        event_type='backup_completed',
        user_id=user_id,
        title=title,
        message=message,
        metadata={'backup_id': backup_id, 'size_mb': size_mb, 'success': success},
    )


@shared_task(name='notifications.notify_replication_issue', queue='fast', soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def notify_replication_issue(
    user_id: int,
    event_type: str,
    mesh_name: str,
    node_name: str,
    wg_address: str,
    lag_bytes: int | None = None,
    message: str = "",
):
    """Fire a replication issue notification (lag or node down).

    event_type must be 'replication_lag' or 'replication_node_down'.
    """
    if event_type == 'replication_node_down':
        title = f"CRITICAL: Replication node {node_name} is DOWN"
        body = (
            f"Node {node_name} ({wg_address}) in mesh '{mesh_name}' is unreachable.\n"
            f"Automatic failover may occur. Check replication health immediately.\n"
            f"{message}"
        )
    elif lag_bytes is not None:
        lag_mb = lag_bytes / (1024 * 1024)
        severity = "CRITICAL" if lag_mb > 10 else "WARNING"
        title = f"{severity}: Replication lag {lag_mb:.1f}MB on {node_name}"
        body = (
            f"Replica {node_name} ({wg_address}) in mesh '{mesh_name}' has "
            f"{lag_mb:.1f}MB of replication lag.\n"
            f"Automatic promotion will trigger at >10MB.\n"
            f"{message}"
        )
    else:
        title = f"Replication issue on {node_name}"
        body = message or f"Unknown replication issue on {node_name} ({wg_address})."

    dispatch_notification.delay(
        event_type=event_type,
        user_id=user_id,
        title=title,
        message=body,
        metadata={
            'mesh_name': mesh_name,
            'node_name': node_name,
            'wg_address': wg_address,
            'lag_bytes': lag_bytes,
        },
    )


# ── Alert Rule Evaluation ────────────────────────────────────────────────────

_RULE_OPERATORS = {
    '>': operator.gt,
    '>=': operator.ge,
    '<': operator.lt,
    '<=': operator.le,
    '==': operator.eq,
    '!=': operator.ne,
}

# Metric types backed by ServiceMetric data. 'disk', 'status',
# 'response_time', and 'error_rate' have no metric source yet.
_RULE_METRICS_WITH_DATA = {'cpu', 'memory'}


def _rule_metric_value(metric_row, metric_type: str):
    """Map a ServiceMetric row to the rule's threshold scale (percent)."""
    if metric_type == 'cpu':
        if float(metric_row.cpu_limit or 0) <= 0:
            return None
        return metric_row.cpu_percent
    if metric_type == 'memory':
        if float(metric_row.memory_limit or 0) <= 0:
            return None
        return metric_row.memory_percent
    return None


def _render_rule_message(rule, value: float, service) -> str:
    template = (rule.message_template or '').strip()
    if template:
        try:
            return template.format(
                metric=rule.metric,
                value=f"{value:.1f}",
                threshold=rule.threshold,
                service=service.name,
            )
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Alert rule %s message template invalid: %s", rule.id, exc)
    return (
        f"Alert rule '{rule.name}' triggered for service '{service.name}': "
        f"{rule.metric} {rule.operator} {rule.threshold} (current: {value:.1f})."
    )


def _dispatch_rule_channel(channel, message: str) -> None:
    """Deliver an alert-rule notification through a NotificationChannel."""
    try:
        if channel.channel_type == 'email':
            from apps.deployments.models import PlatformConfig
            config = PlatformConfig.load()
            if not config.smtp_host or not config.smtp_from_email:
                logger.warning(
                    "Alert rule channel %s: SMTP not configured; skipping email to %s",
                    channel.id,
                    channel.target,
                )
                return
            send_mail(
                subject='[SMSLY] Alert Rule Notification',
                message=message,
                from_email=f"{config.smtp_from_name} <{config.smtp_from_email}>",
                recipient_list=[channel.target],
                fail_silently=False,
            )
        elif channel.channel_type in ('slack', 'webhook'):
            from apps.notifications.webhooks import _post_notification
            ok = _post_notification(channel.target, {'text': message}, provider=channel.channel_type)
            if not ok:
                logger.warning("Alert rule channel %s: webhook delivery rejected", channel.id)
        elif channel.channel_type == 'sms':
            sms_api_url = os.environ.get('SMSLY_SMS_API_URL', '')
            if not sms_api_url:
                logger.warning(
                    "Alert rule channel %s: SMSLY_SMS_API_URL not configured",
                    channel.id,
                )
                return
            payload = {
                'to': channel.target,
                'message': message[:160],
                'source': 'grid-hosting',
                'event_type': 'alert_rule',
                'user_id': '',
            }
            headers = _build_hmac_headers(payload)
            response = requests.post(
                f"{sms_api_url.rstrip('/')}/api/v1/send/",
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Alert rule channel %s (%s) dispatch failed: %s",
            channel.id,
            channel.channel_type,
            exc,
        )


def _fire_alert_rule(rule, service, value: float) -> None:
    """Create the ResourceAlert record, respect cooldown, then dispatch."""
    from django.core.cache import cache
    from apps.notifications.models import ResourceAlert

    cache_key = f"alert_rule_cooldown:{rule.id}:{service.id}"
    if cache.get(cache_key):
        return
    cache.set(cache_key, 1, timeout=max(60, rule.cooldown_minutes * 60))

    message = _render_rule_message(rule, value, service)
    try:
        ResourceAlert.objects.create(
            service=service,
            severity=rule.severity.upper(),
            metric=rule.metric,
            threshold=rule.threshold,
            current_value=value,
            message=message,
        )
    except Exception as exc:
        logger.warning("Failed to persist ResourceAlert for rule %s: %s", rule.id, exc)

    channels = list(rule.channels.filter(enabled=True))
    if channels:
        for channel in channels:
            _dispatch_rule_channel(channel, message)
        return

    if getattr(service, 'owner_id', None):
        dispatch_notification.delay(
            event_type='health_alert',
            user_id=service.owner_id,
            title=f"Alert: {rule.name} — {service.name}",
            message=message,
            metadata={
                'service': service.name,
                'metric': rule.metric,
                'rule': rule.name,
                'current_value': value,
                'threshold': rule.threshold,
                'severity': rule.severity,
            },
        )


@shared_task(name='apps.notifications.tasks.evaluate_alert_rules_task', soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def evaluate_alert_rules_task() -> dict:
    """
    Evaluate enabled AlertRules against recent ServiceMetric rows.

    For every (rule, service) pair whose latest metric breaches the rule
    threshold, creates a ResourceAlert and notifies the rule's channels
    (or the service owner's preferences when the rule has no channels).
    Cooldown is enforced per (rule, service) via cache.
    """
    from apps.autoscaler.models.metrics import ServiceMetric
    from apps.deployments.models import Service
    from apps.notifications.models import AlertRule

    rules = [r for r in AlertRule.objects.filter(enabled=True) if r.metric in _RULE_METRICS_WITH_DATA]
    if not rules:
        return {'evaluated': 0, 'fired': 0}

    cutoff = timezone.now() - timezone.timedelta(minutes=15)
    latest_by_service: dict = {}
    for row in ServiceMetric.objects.filter(timestamp__gte=cutoff).order_by('service_id', '-timestamp'):
        if row.service_id not in latest_by_service:
            latest_by_service[row.service_id] = row

    if not latest_by_service:
        return {'evaluated': 0, 'fired': 0}

    services = {
        s.id: s
        for s in Service.objects.filter(id__in=list(latest_by_service.keys())).only('id', 'name', 'owner_id')
    }

    fired = 0
    for rule in rules:
        operator_fn = _RULE_OPERATORS.get(rule.operator)
        if operator_fn is None:
            continue
        for service_id, metric_row in latest_by_service.items():
            service = services.get(service_id)
            if service is None:
                continue
            value = _rule_metric_value(metric_row, rule.metric)
            if value is None:
                continue
            if operator_fn(value, rule.threshold):
                _fire_alert_rule(rule, service, value)
                fired += 1

    logger.info("Alert rule evaluation complete: %d rule(s) fired", fired)
    return {'evaluated': len(rules) * len(latest_by_service), 'fired': fired}
