import logging

logger = logging.getLogger(__name__)
from celery import shared_task
from django.core.cache import cache

from apps.deployments.constants import TASK_TIME_LIMIT_DEPLOY, TASK_TIME_LIMIT_PROVISION
from apps.deployments.models.transfer import ServerTransfer as TransferModel
from apps.deployments.services.transfer_service import ServerTransferService


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_DEPLOY[0], time_limit=TASK_TIME_LIMIT_DEPLOY[1], name="apps.deployments.tasks.execute_server_transfer_task")
def execute_server_transfer_task(self, transfer_id):
    from apps.deployments.services.transfer_service import (
        ServerTransferService,
        _redact_transfer_text,
    )

    from .models.transfer import ServerTransfer as TransferModel

    lock_key = f"server-transfer:{transfer_id}"
    if not cache.add(lock_key, "1", timeout=3600):
        logger.warning("Transfer Task: duplicate execution ignored for %s", transfer_id)
        return {"status": "skipped", "reason": "already_running"}

    try:
        transfer = TransferModel.objects.get(id=transfer_id)
    except TransferModel.DoesNotExist:
        logger.error("Transfer Task: transfer %s not found", transfer_id)
        cache.delete(lock_key)
        return {"status": "missing"}

    if transfer.status in {"COMPLETED", "FAILED", "ROLLED_BACK", "CANCELLED"}:
        cache.delete(lock_key)
        return {"status": "skipped", "reason": f"terminal:{transfer.status}"}

    try:
        engine = ServerTransferService(transfer)
        engine.execute()
        transfer.refresh_from_db(fields=["status"])
        if transfer.status == "COMPLETED":
            transfer.target_ssh_key = ""
            transfer.target_ssh_password = ""
            transfer.source_ssh_key = ""
            transfer.source_ssh_password = ""
            transfer.save(update_fields=[
                "target_ssh_key",
                "target_ssh_password",
                "source_ssh_key",
                "source_ssh_password",
            ])
        return {"status": transfer.status}
    except Exception as exc:
        logger.exception("Transfer Task: unhandled failure for %s: %s", transfer_id, exc)
        transfer.status = "FAILED"
        transfer.error_message = _redact_transfer_text(str(exc))[:4000]
        transfer.target_ssh_key = ""
        transfer.target_ssh_password = ""
        transfer.source_ssh_key = ""
        transfer.source_ssh_password = ""
        transfer.save(update_fields=[
            "status",
            "error_message",
            "target_ssh_key",
            "target_ssh_password",
            "source_ssh_key",
            "source_ssh_password",
        ])
        return {"status": "FAILED", "error": str(exc)}
    finally:
        cache.delete(lock_key)



@shared_task(bind=True, name="apps.deployments.tasks.rollback_transfer_task", soft_time_limit=TASK_TIME_LIMIT_PROVISION[0], time_limit=TASK_TIME_LIMIT_PROVISION[1])
def rollback_transfer_task(self, transfer_id):

    lock_key = f"server-transfer-rollback:{transfer_id}"
    if not cache.add(lock_key, "1", timeout=1800):
        logger.warning("Transfer Rollback Task: duplicate rollback ignored for %s", transfer_id)
        return {"status": "skipped", "reason": "already_running"}

    try:
        transfer = TransferModel.objects.get(id=transfer_id)
        if transfer.status in {"COMPLETED", "FAILED", "ROLLED_BACK", "CANCELLED"}:
            cache.delete(lock_key)
            return {"status": "skipped", "reason": f"terminal:{transfer.status}"}
        engine = ServerTransferService(transfer)
        engine.rollback()
        return {"status": "ROLLED_BACK"}
    except TransferModel.DoesNotExist:
        logger.error("Transfer Rollback Task: transfer %s not found", transfer_id)
        return {"status": "missing"}
    except Exception as exc:
        logger.exception("Transfer Rollback Task failed for %s: %s", transfer_id, exc)
        return {"status": "FAILED", "error": str(exc)}
    finally:
        cache.delete(lock_key)
