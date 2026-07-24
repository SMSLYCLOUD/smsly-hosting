import logging
from datetime import datetime

import croniter  # type: ignore[import-untyped]
from celery import shared_task
from django.utils import timezone

from apps.deployments.models.cron import CronJob

logger = logging.getLogger(__name__)

# Minimum interval between executions for the same cron job (seconds).
# Prevents rapid-fire runs when a schedule is very tight (e.g. */1 * * * *).
_CRON_MIN_INTERVAL = 60


@shared_task(
    bind=True,
    soft_time_limit=120,
    time_limit=180,
    max_retries=0,
    name="apps.deployments.tasks_cron.check_cron_jobs")
def check_cron_jobs(self):
    """Periodic task: dispatch every due cron job to its own task.

    Registered in Celery beat once per minute. Uses croniter to
    determine which jobs are actually due so a */5 schedule does
    not fire every 60 s.
    """
    now = timezone.now()
    dispatched = 0
    for job in CronJob.objects.filter(is_active=True).select_related('service'):
        try:
            if not job.next_run_at:
                cron = croniter.croniter(job.schedule, now)
                next_dt = cron.get_next(datetime)
                if timezone.is_naive(next_dt):
                    next_dt = timezone.make_aware(next_dt, timezone.get_current_timezone())
                job.next_run_at = next_dt
                job.save(update_fields=['next_run_at', 'updated_at'])

            # Not due yet — skip
            if job.next_run_at > now:
                continue

            # Enforce a minimum interval to prevent tight-schedule abuse
            if job.last_run_at and (now - job.last_run_at).total_seconds() < _CRON_MIN_INTERVAL:
                continue

            # Job is due! Calculate the NEXT time it should run and save it before dispatching
            # to prevent multiple dispatches if the worker is slow.
            cron = croniter.croniter(job.schedule, now)
            next_dt = cron.get_next(datetime)
            if timezone.is_naive(next_dt):
                next_dt = timezone.make_aware(next_dt, timezone.get_current_timezone())
            job.next_run_at = next_dt
            job.save(update_fields=['next_run_at', 'updated_at'])

            trigger_cron_job.delay(job_id=str(job.id))
            dispatched += 1
        except Exception as exc:
            logger.warning("Scheduling check failed for cron job %s: %s", job.id, exc)

    return {'dispatched': dispatched}


@shared_task(
    bind=True,
    soft_time_limit=300,
    time_limit=360,
    max_retries=1,
    default_retry_delay=120,
    name="apps.deployments.tasks_cron.trigger_cron_job")
def trigger_cron_job(self, job_id):
    """Execute a single cron job inside its service container."""
    try:
        job = CronJob.objects.select_related('service').get(id=job_id)
    except CronJob.DoesNotExist:
        logger.warning("Cron job %s not found — skipping", job_id)
        return

    if not job.is_active:
        return

    service = job.service
    now = timezone.now()

    # Find the active deployment container
    latest_deploy = service.deployments.filter(status='ACTIVE').first()
    if not latest_deploy or not latest_deploy.container_id:
        logger.warning("No active container for cron job %s (%s)", job.name, job.id)
        return

    try:
        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        container = client.containers.get(latest_deploy.container_id)
        exit_code, output = container.exec_run(job.command, detach=False)

        logger.info(
            "Cron %s (service=%s) exit=%d output=%.200s",
            job.name, service.name, exit_code,
            (output or b'').decode('utf-8', errors='replace'),
        )

        if job.cloud_destination_id:
            dest = job.cloud_destination
            try:
                import os
                import tempfile

                from apps.deployments.services.backup_service import upload_backup_to_s3

                timestamp = now.strftime('%Y%m%d_%H%M%S')
                s3_key = f"cron-logs/service-{service.id}/job-{job.name.replace(' ', '_')}/{timestamp}.log"

                with tempfile.NamedTemporaryFile(mode='wb', delete=False) as f:
                    f.write(output or b'')
                    path = f.name

                try:
                    success = upload_backup_to_s3(
                        path, dest.bucket, s3_key,
                        endpoint=dest.endpoint, region=dest.region,
                        access_key=dest.access_key, secret_key=dest.secret_key,
                    )
                    if success:
                        logger.info("Uploaded cron log to %s/%s", dest.bucket, s3_key)
                    else:
                        logger.error("Failed to upload cron log for %s: upload_backup_to_s3 returned False", job.name)
                finally:
                    os.unlink(path)
            except Exception as up_exc:
                logger.error("Failed to upload cron log for %s: %s", job.name, up_exc)

    except Exception as exc:
        logger.error("Failed to run cron job %s: %s", job.name, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        # Final failure — still update last_run so it doesn't get
        # stuck in a retry loop forever.
    finally:
        job.last_run_at = now
        job.save(update_fields=['last_run_at', 'updated_at'])
