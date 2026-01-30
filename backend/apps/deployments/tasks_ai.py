from celery import shared_task
from .models import Deployment
from services.smsly_client import smsly_client
from services.smsly_client import smsly_client
from services.ai_engine import DevOpsAgent


@shared_task
def analyze_failure_task(deployment_id):
    """
    Uses Jules AI (via SMSLY Platform) to analyze build logs and suggest fixes.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)

        # Only analyze if we have logs
        if not deployment.build_logs:
            return

        # Call Jules AI
        diagnosis = smsly_client.analyze_logs_sync(deployment.build_logs)
    Uses the AI Engine (Gemini) to analyze build logs.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        agent = DevOpsAgent()

        diagnosis = agent.diagnose_logs(deployment.build_logs)


        # Update deployment with AI insight
        deployment.ai_diagnosis = diagnosis
        deployment.save(update_fields=['ai_diagnosis'])

    except Deployment.DoesNotExist:
        pass
    except Exception as e:
        print(f"Error in analyze_failure_task: {e}")
