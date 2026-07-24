"""
Utility functions for deployment tasks.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def log_event(action: str, target: str = 'none', actor: str = 'system', metadata: dict | None = None):
    from apps.deployments.models.audit import AuditLog
    try:
        meta = metadata or {}
        if 'timestamp' not in meta:
            meta['timestamp'] = timezone.now().isoformat()

        return AuditLog.objects.create(
            actor=actor,
            action=action,
            target=target,
            metadata=meta
        )
    except Exception as e:
        err_str = str(e)
        if 'duplicate key value violates unique constraint' in err_str and 'pkey' in err_str:
            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT setval(
                            pg_get_serial_sequence('deployments_auditlog', 'id'),
                            COALESCE(MAX(id), 1),
                            true
                        )
                        FROM deployments_auditlog
                        """
                    )
                logger.warning(
                    "AuditLog sequence drift detected and corrected - retrying insert. "
                    "Run migration 0149_reset_auditlog_sequence to make this permanent."
                )
                return AuditLog.objects.create(
                    actor=actor,
                    action=action,
                    target=target,
                    metadata=meta
                )
            except Exception as retry_exc:
                logger.error(f"AuditLog creation failed after sequence reset: {retry_exc}")
                return None
        logger.error(f"AuditLog creation failed: {e}")
        return None
