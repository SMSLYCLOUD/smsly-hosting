import logging

logger = logging.getLogger(__name__)
import subprocess
from datetime import timedelta

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.constants import (
    REGISTRY_TAG_RETENTION_DAYS,
    ROLLBACK_RETAIN_DEPLOYMENTS,
    TASK_TIME_LIMIT_MEDIUM,
    TASK_TIME_LIMIT_QUICK,
    TASK_TIME_LIMIT_STANDARD,
)

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
    _prune_expired_registry_tags()
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


# ── Registry tag retention ──────────────────────────────────────────
# GC alone never reclaims space while old manifests stay tagged, so each
# redeploy permanently grows the registry. This pass deletes manifests
# older than REGISTRY_TAG_RETENTION_DAYS (default 7d, tunable via env of
# the same name), except tags pinned for rollback (newest N successful
# deployments per service, N from PlatformConfig.rollback_retain_deployments
# defaulting to ROLLBACK_RETAIN_DEPLOYMENTS). The GC pass above then
# reclaims the orphaned blobs. Best-effort: any failure only skips the
# retention pass, never the GC.

# Deployment states whose images must never be deleted (in-flight or staged).
# ACTIVE is handled separately by the retain count below.
_PINNED_DEPLOYMENT_STATUSES = frozenset({
    "QUEUED", "REVIEW", "BUILDING", "BACKUP_RUNNING", "MIGRATION_PLANNING",
    "MIGRATION_RUNNING", "DEPLOYING", "HEALTH_CHECK", "ROLLING_BACK",
    "STAGED",
})

_MANIFEST_ACCEPT = (
    "application/vnd.docker.distribution.manifest.v2+json,"
    "application/vnd.oci.image.manifest.v1+json"
)


def _retention_days() -> int:
    import os

    try:
        return max(1, int(os.environ.get(
            "REGISTRY_TAG_RETENTION_DAYS", REGISTRY_TAG_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return REGISTRY_TAG_RETENTION_DAYS


def _rollback_retain_count() -> int:
    try:
        from apps.deployments.models import PlatformConfig
        value = int(getattr(PlatformConfig.load(), "rollback_retain_deployments", 0) or 0)
        if value > 0:
            return min(value, 10)
    except Exception:
        pass
    return ROLLBACK_RETAIN_DEPLOYMENTS


def _rollback_protected_tags(retain: int) -> set:
    """Tags pinned for rollback: the live ACTIVE deployment, the newest
    ``retain - 1`` superseded (INACTIVE) deployments per service — INACTIVE
    is exactly "previously ACTIVE", set by Deployment.save() demotion —
    plus every non-terminal/staged deployment. Returns
    ``{(repo, tag)}`` with repo like ``smsly/my-svc`` and tag ``abc1234``.
    """
    from apps.deployments.models import Deployment
    from apps.deployments.services.registry_credentials import (
        project_image_namespace,
    )

    protected = set()
    try:
        deployments = list(Deployment.objects.select_related("service").order_by("-created_at")[:500])
    except Exception as exc:
        logger.warning("rollback retention: deployment query failed: %s", exc)
        return protected
    kept_inactive: dict = {}
    for dep in deployments:
        service = getattr(dep, "service", None)
        commit = (getattr(dep, "commit_hash", "") or "").strip()
        if service is None or len(commit) < 7:
            continue
        try:
            repo = f"{project_image_namespace(service)}/{str(service.name).lower()}"
        except Exception:
            continue
        tag = commit[:7]
        raw_status = getattr(dep, "status", "")
        status = getattr(raw_status, "value", raw_status)
        if status in _PINNED_DEPLOYMENT_STATUSES:
            protected.add((repo, tag))
            continue
        if status == Deployment.Status.ACTIVE or status == "ACTIVE":
            protected.add((repo, tag))
            continue
        if status == Deployment.Status.INACTIVE or status == "INACTIVE":
            key = str(service.id)
            kept = kept_inactive.get(key, 0)
            if kept < max(0, retain - 1):
                protected.add((repo, tag))
                kept_inactive[key] = kept + 1
    return protected


def select_expired_registry_tags(repo_tags, now, retention_days, protected) -> list:
    """Pure selection: which (repo, tag, digest) entries may be deleted.

    ``repo_tags`` maps repo -> list of dicts with tag/digest/created
    (created datetime or None when unknown). Keeps protected tags and
    anything of unknown age or within retention. Unit-tested.
    """
    cutoff = now - timedelta(days=retention_days)
    expired = []
    for repo, tags in (repo_tags or {}).items():
        for entry in tags or []:
            tag = entry.get("tag")
            digest = entry.get("digest")
            if not tag or not digest:
                continue
            if (repo, tag) in protected:
                continue
            created = entry.get("created")
            if created is None:
                continue
            if created < cutoff:
                expired.append((repo, tag, digest))
    return expired


def _registry_session():
    import requests
    from django.conf import settings

    base = str(getattr(settings, "CONTAINER_REGISTRY_URL", "") or "").strip()
    if not base:
        return None, ""
    host = base.split("://")[-1]
    session = requests.Session()
    user = str(getattr(settings, "REGISTRY_USER", "") or "")
    password = str(getattr(settings, "REGISTRY_PASSWORD", "") or "")
    if user:
        session.auth = (user, password)
    # The private registry speaks TLS on some installs (live: plain http
    # to :5000 answers "Client sent an HTTP request to an HTTPS server").
    # Prefer https (verified), then https unverified for host-local
    # self-signed certs, then plain http. Traffic never leaves the host.
    candidate = f"https://{host}"
    try:
        probe = session.get(candidate + "/v2/", timeout=10)
        if probe.status_code in (200, 401):
            return session, candidate
    except requests.exceptions.SSLError:
        try:
            probe = session.get(candidate + "/v2/", timeout=10, verify=False)
            if probe.status_code in (200, 401):
                logger.warning("registry retention: registry TLS cert unverified (host-local, continuing)")
                session.verify = False
                return session, candidate
        except requests.RequestException:
            pass
    except requests.RequestException:
        pass
    return session, f"http://{host}"


def _prune_expired_registry_tags() -> None:
    """Delete expired registry tags, then let the GC pass reclaim blobs."""
    import requests

    retention_days = _retention_days()
    retain = _rollback_retain_count()
    try:
        session, base = _registry_session()
        if session is None:
            logger.debug("registry retention: no registry configured, skipping")
            return
        catalog = session.get(base + "/v2/_catalog", timeout=15)
        if catalog.status_code == 401:
            logger.warning("registry retention: registry requires auth we don't have, skipping")
            return
        catalog.raise_for_status()
        repos = (catalog.json() or {}).get("repositories", []) or []
    except Exception as exc:
        logger.warning("registry retention: catalog unreachable, skipping: %s", exc)
        return

    from django.utils import timezone as _tz

    repo_tags: dict = {}
    for repo in repos:
        try:
            tags_resp = session.get(base + f"/v2/{repo}/tags/list", timeout=15)
            if tags_resp.status_code != 200:
                continue
            for tag in (tags_resp.json() or {}).get("tags", []) or []:
                manifest = session.get(
                    base + f"/v2/{repo}/manifests/{tag}",
                    headers={"Accept": _MANIFEST_ACCEPT}, timeout=15,
                )
                if manifest.status_code != 200:
                    continue
                digest = manifest.headers.get("Docker-Content-Digest", "")
                created = None
                try:
                    config_digest = (manifest.json() or {}).get("config", {}).get("digest", "")
                    if config_digest:
                        blob = session.get(
                            base + f"/v2/{repo}/blobs/{config_digest}", timeout=15)
                        if blob.status_code == 200:
                            created_raw = (blob.json() or {}).get("created", "")
                            if created_raw:
                                created = _tz.datetime.fromisoformat(
                                    created_raw.replace("Z", "+00:00"))
                except Exception:
                    created = None
                repo_tags.setdefault(repo, []).append(
                    {"tag": tag, "digest": digest, "created": created})
        except requests.RequestException as exc:
            logger.debug("registry retention: repo %s skipped: %s", repo, exc)
            continue

    protected = _rollback_protected_tags(retain)
    expired = select_expired_registry_tags(repo_tags, _tz.now(), retention_days, protected)
    if not expired:
        logger.info("registry retention: nothing older than %dd outside rollback window (retain=%d)",
                    retention_days, retain)
        return
    deleted = 0
    for repo, tag, digest in expired:
        try:
            resp = session.delete(base + f"/v2/{repo}/manifests/{digest}", timeout=15)
            if resp.status_code in (200, 202):
                deleted += 1
            else:
                logger.debug("registry retention: delete %s:%s -> %s", repo, tag, resp.status_code)
        except requests.RequestException as exc:
            logger.debug("registry retention: delete %s:%s failed: %s", repo, tag, exc)
    logger.info("registry retention: deleted %d expired tag(s) older than %dd (retain=%d rollback each)",
                deleted, retention_days, retain)


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
