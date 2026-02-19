"""Tasks Ai module."""
import logging

from celery import shared_task
from .models import Deployment
from services.ai_engine import DevOpsAgent

logger = logging.getLogger(__name__)


@shared_task
def analyze_failure_task(deployment_id):
    """
    Uses Jules AI (via SMSLY Platform) to analyze build logs and suggest fixes.
    Uses the AI Engine (Gemini) to analyze build logs.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)

        # Only analyze if we have logs
        if not deployment.build_logs:
            return {"status": "skipped", "reason": "no_build_logs"}

        # Call Jules AI
        agent = DevOpsAgent()
        diagnosis = agent.diagnose_logs(deployment.build_logs)

        # Update deployment with AI insight
        deployment.ai_diagnosis = diagnosis
        deployment.save(update_fields=['ai_diagnosis'])
        return {"status": "ok", "deployment_id": str(deployment.id)}

    except Deployment.DoesNotExist:
        logger.warning("analyze_failure_task skipped: deployment %s not found", deployment_id)
        return {"status": "skipped", "reason": "deployment_not_found"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Error in analyze_failure_task for %s: %s", deployment_id, exc)
        return {"status": "error", "reason": str(exc)}
