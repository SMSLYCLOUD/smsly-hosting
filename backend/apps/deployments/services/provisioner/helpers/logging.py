import logging
import re
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from apps.deployments.models.servers import ManagedServer

logger = logging.getLogger(__name__)


def _broadcast_provision_log(server: ManagedServer, message: str):
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"provision_{server.id}",
            {
                "type": "provision.log",
                "message": message,
            },
        )
    except Exception as exc:
        logger.debug("Failed to send provision log via channel layer: %s", exc)


# Cap retained provision logs: thousands of per-line DB writes over a
# 15-minute provision bloat the row without bound otherwise.
PROVISION_LOGS_KEEP_CHARS = 200_000


def _append_log(server: ManagedServer, line: str):
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
    correlation_id = getattr(server, "_provision_correlation_id", None)
    if not correlation_id:
        correlation_id = str(uuid.uuid4())[:8]
        server._provision_correlation_id = correlation_id
    line = re.sub(r'(?i)(password|passwd|secret|token|key)\s*[=:]\s*\S+', r'\1=[REDACTED]', line)
    line = re.sub(r'([A-Za-z0-9+/=]{40,})', r'[REDACTED]', line)
    line = re.sub(r'([0-9a-f]{32,})', r'[REDACTED]', line)
    formatted_line = f"[{timestamp}] [tx:{correlation_id}] {line}"
    combined = (server.provision_logs or "") + formatted_line + "\n"
    if len(combined) > PROVISION_LOGS_KEEP_CHARS:
        combined = "[... truncated older logs ...]\n" + combined[-PROVISION_LOGS_KEEP_CHARS:]
    server.provision_logs = combined
    server.save(update_fields=["provision_logs", "updated_at"])
    _broadcast_provision_log(server, formatted_line)
