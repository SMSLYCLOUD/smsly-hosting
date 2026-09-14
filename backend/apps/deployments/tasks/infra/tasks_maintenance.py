import logging

logger = logging.getLogger(__name__)
import subprocess

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM, TASK_TIME_LIMIT_QUICK, TASK_TIME_LIMIT_STANDARD

from ..deploy.deletion import (  # noqa: F401
    _clear_orphaned_runtime_resources,
    _extract_addon_id_from_name,
    _is_stale_maintenance_container,
)
from ..remote.update import ThrottledLogAppender  # noqa: F401
from .tasks_platform_update import platform_update_task


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1], name="apps.deployments.tasks.run_maintenance_task")
def run_maintenance_task(self, command_flag: str, lock_key: str = ""):
    """
    Run maintenance commands via the Docker API from inside the Celery container.
    Valid flags: --clear, --update, --refresh
    """
    if command_flag not in ['--clear', '--update', '--update-frontend', '--refresh']:
        logger.error(f"Invalid maintenance command: {command_flag}")
        return {"status": "error", "reason": "invalid_command", "message": "Invalid maintenance command."}

    try:
        logger.info(f"Running maintenance command: {command_flag}")
        self.update_state(
            state="STARTED",
            meta={
                "status": "running",
                "message": f"Running maintenance command {command_flag}.",
            },
        )

        if command_flag == '--clear':
            details = _clear_orphaned_runtime_resources()
            return {
                "status": "success",
                "message": (
                    "Cleanup complete. Removed "
                    f"{details['removed_count']} orphaned container(s) and flushed cache directories."
                ),
                "details": details,
            }

        elif command_flag == '--refresh':
            from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile

            from apps.deployments.models import PlatformConfig

            config = PlatformConfig.load()
            content = generate_caddyfile(config)
            cf_token = (getattr(config, "cloudflare_api_token", "") or "").strip()

            result = apply_caddyfile(content, cloudflare_token=cf_token)
            if result.get('ok'):
                logger.info("Proxy refresh flag written to shared volume successfully.")
                return {
                    "status": "success",
                    "message": "Proxy refresh flag written. The host will reload Caddy shortly.",
                    "details": result,
                }
            else:
                return {
                    "status": "error",
                    "message": result.get('message', 'Failed to write proxy reload flag.'),
                    "details": result,
                }

        elif command_flag in ['--update', '--update-frontend']:
            from .models.updates import PlatformUpdate

            stale_in_progress = PlatformUpdate.objects.filter(
                status__in=['PENDING', 'PULLING', 'BACKING_UP', 'RESTARTING', 'HEALTH_CHECK', 'MIGRATING']
            )
            if stale_in_progress.exists():
                cleared_count = 0
                for stale in stale_in_progress:
                    stale.status = 'FAILED'
                    stale.error_message = 'Cleared stale update to allow new update to proceed.'
                    stale.completed_at = timezone.now()
                    stale.append_log('✗ Cleared as stale to allow new update to proceed.')
                    stale.save()
                    cleared_count += 1
                    logger.info("Cleared stale platform update %s (was %s)", stale.id, stale.status)

                if cleared_count:
                    self.update_state(
                        state="STARTED",
                        meta={
                            "status": "running",
                            "message": f"Cleared {cleared_count} stale update(s). Starting fresh update...",
                        },
                    )

            update = PlatformUpdate.objects.create(
                initiated_by='system_maintenance',
                current_step='Initiating via maintenance task'
            )

            platform_update_task.delay(update_id=str(update.id))

            logger.info(f"Platform update {update.id} initiated via maintenance action.")
            return {
                "status": "success",
                "message": "Platform update initiated using the resilient updater. You can track progress in the Platform Updates log.",
                "task_id": str(update.id)
            }

    except Exception as e:
        logger.exception(f"Exception during maintenance {command_flag}: {e}")
        return {"status": "error", "reason": str(e), "message": f"Maintenance failed: {e}"}
    finally:
        if lock_key:
            cache.delete(lock_key)



@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.registry_garbage_collection_task")
def registry_garbage_collection_task():
    """
    Periodically run Docker registry garbage collection to reclaim disk
    space from deleted/unused image layers.

    Runs: docker exec <registry> registry garbage-collect /etc/docker/registry/config.yml
    Removes blobs that are no longer referenced by any manifest.
    Safe to run while the registry is serving reads.
    """
    registry_container = "smsly-hosting-registry-1"

    try:
        dry_run = subprocess.run(
            ["docker", "exec", registry_container, "registry", "garbage-collect",
             "--dry-run", "/etc/docker/registry/config.yml"],
            capture_output=True, text=True, timeout=120,
        )
        if dry_run.returncode != 0:
            logger.warning("registry_gc: dry-run failed: %s", dry_run.stderr[:500])
            return

        freed_lines = [line for line in dry_run.stdout.split('\n') if 'marking blob' in line.lower() or 'blob eligible' in line.lower()]
        logger.info("registry_gc: dry-run found %d blobs eligible for removal", len(freed_lines))

        result = subprocess.run(
            ["docker", "exec", registry_container, "registry", "garbage-collect",
             "/etc/docker/registry/config.yml"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if 'recovered' in line.lower() or 'blob' in line.lower():
                    logger.info("registry_gc: %s", line.strip())
            logger.info("registry_gc: garbage collection completed successfully")
        else:
            logger.warning("registry_gc: failed: %s", result.stderr[:500])

    except subprocess.TimeoutExpired:
        logger.error("registry_gc: timed out")
    except Exception as e:
        logger.error("registry_gc: error: %s", e)


@shared_task(soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], name="apps.deployments.tasks.reconcile_network_isolation_task")
def reconcile_network_isolation_task():
    """Self-healing pass over scoped-network isolation.

    * Purges DOCKER-USER rules whose bridge interface no longer exists
    * Reapplies egress isolation to live paas-svc-* bridges missing their tag
      (closes the recreate-gap: a freshly recreated bridge starts unrestricted)
    * Ensures Traefik is attached to every scoped bridge

    Registered in celery.py beat_schedule every 10 minutes.
    """
    try:
        from apps.deployments.services.network_scope import reconcile_network_isolation
        stats = reconcile_network_isolation()
        logger.info("network isolation reconcile: %s", stats)
        return {"status": "ok", **stats}
    except Exception as e:
        logger.error("network isolation reconcile failed: %s", e)
        return {"status": "error", "reason": str(e)}


@shared_task(soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], name="apps.deployments.tasks.ensure_service_network_attachments")
def ensure_service_network_attachments():
    """Attach live service containers missing their scoped project network.

    Heals the gap where an app container serves on plain ``smsly-net``
    while its addons live on the project bridge (``smsly-net-<scope8>``):
    Docker DNS cannot resolve addon aliases such as ``redis-shared`` and
    the service 503s while the deployment row says ACTIVE (2026-09-14
    live incident: identity-service). Compose-mode services are skipped
    (their networks come from compose files). Idempotent: already-attached
    containers are a no-op. Registered in celery.py beat_schedule every
    15 minutes.
    """
    try:
        import docker

        from apps.deployments.models import Service
        from apps.deployments.models.network_scope import ScopedNetwork
        from apps.deployments.services.network_scope import (
            attach_container_to_service_network,
        )
    except Exception as e:
        logger.error("service-network repair: imports failed: %s", e)
        return {"status": "error", "reason": str(e)}

    checked = 0
    ensured = 0
    failed = 0
    skipped_compose = 0
    try:
        client = docker.from_env()
    except Exception as e:
        logger.error("service-network repair: docker unavailable: %s", e)
        return {"status": "error", "reason": str(e)}

    try:
        services = Service.objects.filter(
            project__isnull=False,
        ).select_related("project")
        for service in services.iterator():
            try:
                if getattr(service, "deploy_mode", "") == "COMPOSE":
                    skipped_compose += 1
                    continue
                network_name = ScopedNetwork.resolve_network_name(service.project)
                if not network_name or network_name == "smsly-net":
                    continue
                container = None
                container_id = getattr(service, "active_runtime_id", "") or ""
                if container_id:
                    try:
                        container = client.containers.get(container_id)
                    except docker.errors.NotFound:
                        container = None
                if container is None:
                    try:
                        container = client.containers.get(service.name)
                    except docker.errors.NotFound:
                        continue
                checked += 1
                if attach_container_to_service_network(service, container.id):
                    ensured += 1
                else:
                    failed += 1
            except Exception:
                logger.exception(
                    "service-network repair failed for service %s",
                    getattr(service, "name", "?"),
                )
                failed += 1
    except Exception as e:
        logger.error("service-network repair sweep failed: %s", e)
        return {"status": "error", "reason": str(e)}

    logger.info(
        "service-network repair: checked=%d ensured=%d failed=%d skipped_compose=%d",
        checked, ensured, failed, skipped_compose,
    )
    return {
        "status": "ok",
        "checked": checked,
        "ensured": ensured,
        "failed": failed,
        "skipped_compose": skipped_compose,
    }


@shared_task(soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], name="apps.deployments.tasks.ensure_addon_network_aliases")
def ensure_addon_network_aliases():
    """Re-affirm addon DNS aliases (e.g. ``redis-shared``) on scoped bridges.

    Live incident 2026-09-14: the ``redis-shared`` alias flapped on
    ``smsly-net-96e85eee`` (present → absent → present, endpoint IDs
    churning, no docker-events trace). Apps with pooled connections masked
    it until pools recycled, then every Redis call failed with
    ``Name or service not known``. Deploy-time repair
    (``_ensure_addons_ready``) only runs during deploys, so a mid-life
    alias strip never healed.

    This beat task sweeps every ACTIVE addon with a connection URL,
    resolves the URL hostname apps actually dial, and re-attaches it with
    ``docker network connect --alias`` when missing. Idempotent no-op when
    the alias is present; never touches stopped/missing containers beyond
    counting them. Registered in celery.py beat_schedule every 15 minutes.
    """
    import subprocess
    from urllib.parse import unquote
    from urllib.parse import urlparse as _urlparse

    try:
        from apps.deployments.models.addons import Addon
        from apps.deployments.models.network_scope import ScopedNetwork
    except Exception as e:
        logger.error("addon-alias guard: imports failed: %s", e)
        return {"status": "error", "reason": str(e)}

    checked = 0
    repaired = 0
    failed = 0
    skipped = 0
    try:
        addons = Addon.objects.filter(status="ACTIVE").select_related(
            "service", "service__project",
        )
        addon_list = list(addons.iterator())
    except Exception as e:
        logger.error("addon-alias guard: DB query failed: %s", e)
        return {"status": "error", "reason": str(e)}

    for addon in addon_list:
        try:
            url = (getattr(addon, "connection_url", "") or "").strip()
            if not url:
                skipped += 1
                continue
            hostname = unquote(_urlparse(url).hostname or "").strip().lower()
            if not hostname:
                skipped += 1
                continue
            container_name = (
                f"smsly-addon-{str(getattr(addon, 'addon_type', '')).lower()}"
                f"-{addon.id}"
            )
            try:
                inspect = subprocess.run(
                    ["docker", "inspect", "-f",
                     "{{range .NetworkSettings.Networks}}{{range .Aliases}}{{.}} {{end}}{{end}}",
                     container_name],
                    capture_output=True, text=True, timeout=15,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.debug("addon-alias guard: inspect failed for %s: %s", container_name, exc)
                failed += 1
                continue
            if inspect.returncode != 0:
                # Container gone (addon scaled down / mid-reprovision) —
                # nothing to repair; the next provision re-attaches.
                skipped += 1
                continue
            aliases = {a.lower() for a in (inspect.stdout or "").split()}
            checked += 1
            if hostname in aliases:
                continue
            # Alias missing — re-attach on the scoped bridge when the
            # addon has a project, else the shared net.
            project = getattr(addon, "project", None) or getattr(
                getattr(addon, "service", None), "project", None,
            )
            target_net = ""
            if project is not None:
                try:
                    target_net = ScopedNetwork.resolve_network_name(project)
                except Exception:
                    target_net = ""
            if not target_net:
                try:
                    from apps.addons.services.addon_provisioner import (
                        addon_provisioner,
                    )
                    target_net = addon_provisioner.network_name
                except Exception:
                    target_net = "smsly-net"
            try:
                repair = subprocess.run(
                    ["docker", "network", "connect", "--alias", hostname,
                     target_net, container_name],
                    capture_output=True, text=True, timeout=30,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning(
                    "addon-alias guard: repair failed for %s (alias %s): %s",
                    container_name, hostname, exc,
                )
                failed += 1
                continue
            if repair.returncode == 0 or "already exists" in (repair.stderr or "").lower():
                repaired += 1
                logger.info(
                    "addon-alias guard: restored alias %s for %s on %s",
                    hostname, container_name, target_net,
                )
            else:
                failed += 1
                logger.warning(
                    "addon-alias guard: could not restore alias %s for %s: %s",
                    hostname, container_name, (repair.stderr or "").strip()[:200],
                )
        except Exception:
            logger.exception(
                "addon-alias guard failed for addon %s",
                getattr(addon, "name", "?"),
            )
            failed += 1

    logger.info(
        "addon-alias guard: checked=%d repaired=%d failed=%d skipped=%d",
        checked, repaired, failed, skipped,
    )
    return {
        "status": "ok",
        "checked": checked,
        "repaired": repaired,
        "failed": failed,
        "skipped": skipped,
    }


@shared_task(soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1], name="apps.deployments.tasks.cleanup_orphaned_containers_task")
def cleanup_orphaned_containers_task():
    """Periodic orphan sweep: stale green candidates (stopped OR running),
    expired rollback backups, containers for services/addons missing from
    the DB, and unused image layers.

    Before this was scheduled, orphaned greens from crashed promotes sat
    "Up (unhealthy)" for DAYS — the old rule only ever collected STOPPED
    containers, and nothing invoked the cleanup on a schedule at all.
    Registered in celery.py beat_schedule every 30 minutes.
    """
    try:
        details = _clear_orphaned_runtime_resources()
        removed = details.get("removed", [])
        if removed:
            logger.info(
                "orphan sweep removed %d container(s): %s",
                len(removed),
                ", ".join(r.get("name", "?") for r in removed[:10]),
            )
        else:
            logger.info("orphan sweep: nothing to remove")
        return {"status": "ok", **details}
    except Exception as e:
        logger.error("orphan sweep failed: %s", e)
        return {"status": "error", "reason": str(e)}
