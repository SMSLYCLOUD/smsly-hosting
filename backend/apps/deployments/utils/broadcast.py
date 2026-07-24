"""
WebSocket broadcasting and log append utilities.
"""
import logging
import re
import uuid

from django.utils import timezone

logger = logging.getLogger(__name__)


def broadcast_log(deployment, log_line):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'build_log',
                    'log': log_line,
                    'status': deployment.status,
                    'timestamp': timezone.now().isoformat(),
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast log: %s", e)


def broadcast_status(deployment):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'status_change',
                    'status': deployment.status,
                    'finished_at': (
                        deployment.finished_at.isoformat()
                        if deployment.finished_at else ''
                    ),
                    'duration_seconds': deployment.duration_seconds,
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast status: %s", e)


def broadcast_pipeline(deployment):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer:
            group_name = f"build_logs_{deployment.id}"
            async_to_sync(channel_layer.group_send)(
                group_name,
                {
                    'type': 'pipeline_update',
                    'stages': deployment.pipeline_stages,
                }
            )
    except Exception as e:
        logger.debug("Failed to broadcast pipeline: %s", e)


def update_stage(deployment, name, status, duration=None):
    deployment.refresh_from_db(fields=['pipeline_stages'])
    stages = deployment.pipeline_stages or []
    if not isinstance(stages, list):
        stages = []

    found = False
    for stage in stages:
        if stage.get('name') == name:
            stage['status'] = status
            if duration is not None:
                stage['duration'] = duration
            found = True
            break

    if not found:
        stages.append({
            'name': name,
            'status': status,
            'duration': duration or 0
        })

    deployment.pipeline_stages = stages
    deployment.save(update_fields=['pipeline_stages'])
    broadcast_pipeline(deployment)


def append_log(deployment, log_line):
    if not log_line:
        return

    correlation_id = getattr(deployment, "_deploy_correlation_id", None)
    if not correlation_id:
        correlation_id = str(uuid.uuid4())[:8]
        deployment._deploy_correlation_id = correlation_id
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")

    sanitized_log = str(log_line).replace('\x00', '')
    sanitized_log = f"[{timestamp}] [tx:{correlation_id}] {sanitized_log}"

    sanitized_log = re.sub(r"(redis(?:s)?://(?:[^:]*:)?)[^@]+(@)", r"\1***\2", sanitized_log)
    sanitized_log = re.sub(r"(postgres(?:ql)?://(?:[^:]*:)?)[^@]+(@)", r"\1***\2", sanitized_log)
    sanitized_log = re.sub(r"(?i)(password|secret|token|api_key)=([^\s&]+)", r"\1=***", sanitized_log)

    deployment.refresh_from_db(fields=['build_logs'])
    deployment.build_logs += sanitized_log
    deployment.save(update_fields=['build_logs'])
    broadcast_log(deployment, sanitized_log)
