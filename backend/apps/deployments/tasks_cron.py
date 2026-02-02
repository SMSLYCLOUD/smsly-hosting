import logging
from celery import shared_task
from django.utils import timezone
from .models_cron import CronJob
from apps.cloud.adapters.local import LocalAdapter

logger = logging.getLogger(__name__)

@shared_task
def check_cron_jobs():
    """
    Periodic task to check for due cron jobs.
    This should be run every minute by Celery Beat.
    """
    now = timezone.now()
    jobs = CronJob.objects.filter(is_active=True)

    # In a real implementation, we would use a cron library to check
    # if the 'schedule' matches 'now'. For now, we simulate execution
    # if it hasn't run in the last X minutes.

    for job in jobs:
        # Simplification: Assume all jobs run every minute for demo
        # Real logic: if croniter(job.schedule).is_due(now): ...

        trigger_cron_job.delay(str(job.id))

@shared_task
def trigger_cron_job(job_id):
    try:
        job = CronJob.objects.get(id=job_id)
        service = job.service

        # Find active deployment container
        latest_deploy = service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy or not latest_deploy.container_id:
            logger.warning(f"No active container for cron job {job.name}")
            return

        adapter = LocalAdapter()
        # Use exec_container to run the command
        # Note: This is simplified. exec_container returns a socket for interactive use.
        # We need a non-interactive exec.
        # We'll assume the adapter has a method or we'll add one.

        # Let's use docker client directly for one-off exec if needed,
        # or enhance adapter.
        if adapter.docker_client:
             container = adapter.docker_client.containers.get(latest_deploy.container_id)
             exit_code, output = container.exec_run(job.command, detach=False)

             logger.info(f"Cron {job.name} finished with exit code {exit_code}. Output: {output.decode('utf-8')[:100]}...")

             job.last_run_at = timezone.now()
             job.save()

    except Exception as e:
        logger.error(f"Failed to run cron job {job_id}: {e}")
