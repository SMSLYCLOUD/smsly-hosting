import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

def send_slack_notification(message: str, webhook_url: str = None):
    """Send a notification to a Slack webhook."""
    url = webhook_url or getattr(settings, 'SLACK_WEBHOOK_URL', None)
    if not url:
        return

    try:
        requests.post(url, json={"text": message}, timeout=5)
    except Exception as e:
        logger.error(f"Failed to send Slack notification: {e}")

def send_discord_notification(message: str, webhook_url: str = None):
    """Send a notification to a Discord webhook."""
    url = webhook_url or getattr(settings, 'DISCORD_WEBHOOK_URL', None)
    if not url:
        return

    try:
        requests.post(url, json={"content": message}, timeout=5)
    except Exception as e:
        logger.error(f"Failed to send Discord notification: {e}")
