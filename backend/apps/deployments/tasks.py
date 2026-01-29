from celery import shared_task
from services.orchestrator import Orchestrator

@shared_task
def run_deployment_task(deployment_id):
    orchestrator = Orchestrator(deployment_id)
    orchestrator.run_deployment()
