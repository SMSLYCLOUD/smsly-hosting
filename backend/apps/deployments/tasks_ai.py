"""Tasks Ai module."""
from celery import shared_task
from .models import Deployment
<<<<<<< Updated upstream
from services.ai_engine import DevOpsAgent

=======
from services.smsly_client import smsly_client
>>>>>>> Stashed changes

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
            return

        # Call Jules AI
<<<<<<< Updated upstream
        # Prioritize DevOpsAgent if available, fallback to smsly_client
        agent = DevOpsAgent()
        diagnosis = agent.diagnose_logs(deployment.build_logs)
=======
        diagnosis = smsly_client.analyze_logs_sync(deployment.build_logs)
>>>>>>> Stashed changes

        # Update deployment with AI insight
        deployment.ai_diagnosis = diagnosis
        deployment.save(update_fields=['ai_diagnosis'])

    except Deployment.DoesNotExist:
        pass
    except Exception as e:
        print(f"Error in analyze_failure_task: {e}")
