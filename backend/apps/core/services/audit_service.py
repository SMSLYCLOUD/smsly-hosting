import logging

from apps.core.models.audit import AuditLog

logger = logging.getLogger(__name__)

class AuditService:
    """Service to record system-wide audit events."""

    @classmethod
    def log(cls, action, actor="system", target="none", metadata=None, status="success", error_code=None, message=None):
        if metadata is None:
            metadata = {}

        metadata["status"] = status
        if error_code:
            metadata["error_code"] = error_code
        if message:
            metadata["message"] = message

        try:
            audit = AuditLog.objects.create(
                actor=actor,
                action=action,
                target=target,
                metadata=metadata
            )

            log_data = {
                "actor": actor,
                "action": action,
                "target": target,
                "status": status,
                "error_code": error_code,
                "message": message,
                "metadata": metadata,
                "audit_id": audit.id,
                "audit_hash": audit.hash
            }
            logger.info(f"AUDIT_EVENT: {log_data}")
            return audit
        except Exception as e:
            logger.error(f"Failed to write audit log for action {action}: {e}")
            return None
