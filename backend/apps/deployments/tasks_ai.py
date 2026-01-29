from celery import shared_task
from .models import Deployment

@shared_task
def analyze_failure_task(deployment_id):
    """
    Simulates an AI agent analyzing build logs to suggest fixes.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        logs = deployment.build_logs.lower()

        diagnosis = ""

        # Heuristics (simulating LLM)
        if "requirements.txt: no such file" in logs or "command not found: pip" in logs:
            diagnosis = "It seems you are missing a `requirements.txt` file or using the wrong base image for Python."
        elif "package.json: no such file" in logs or "command not found: npm" in logs:
            diagnosis = "It seems you are missing a `package.json` file. Ensure you are in the root directory."
        elif "connection refused" in logs:
            diagnosis = "Database connection failed. Check your environment variables and Ensure your Add-on is provisioned."
        elif "permission denied" in logs:
            diagnosis = "Script execution permission denied. Try running `chmod +x` on your entrypoint script."
        else:
            diagnosis = "Generic Build Failure. Please check syntax errors in your code or Dockerfile."

        deployment.ai_diagnosis = diagnosis
        deployment.save(update_fields=['ai_diagnosis'])

    except Deployment.DoesNotExist:
        pass
