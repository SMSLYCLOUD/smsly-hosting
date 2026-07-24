import logging

logger = logging.getLogger(__name__)
import logging

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.services.task_encryption import decrypt_arg


def _bounded_error(exc, limit=2000):
    return str(exc).replace("\x00", "")[:limit]


@shared_task(
    name="apps.deployments.tasks_replication.check_replication_health_task",
    soft_time_limit=300,
    time_limit=360)
def check_replication_health_task():
    """
    Periodic task (every 30s): check WAL replication lag across all meshes.

    Logs warnings if lag exceeds thresholds:
    - > 1MB: warning
    - > 10MB: critical alert
    - Node unreachable: error

    C3: Also checks Patroni leader status and triggers ElectionService
    re-election if Patroni reports no leader while election still has one.
    H6: Dispatches replication_lag / replication_node_down notifications
    with 15-minute rate limiting per node to avoid alert storms.
    """
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.replication_service import ReplicationService

    RATE_LIMIT_KEY_PREFIX = "replication-alert:"
    RATE_LIMIT_SECONDS = 900  # 15 minutes

    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        if mesh.peers.filter(is_active=True).count() < 2:
            continue  # No replication possible with 1 peer

        try:
            health = ReplicationService.check_replication_health(mesh)

            for node in health.get("nodes", []):
                if "UNREACHABLE" in node.get("status", ""):
                    logger.error(
                        f"Replication node {node['name']} ({node['wg_address']}) "
                        f"is UNREACHABLE"
                    )
                    _dispatch_replication_alert(
                        mesh, node,
                        event_type="replication_node_down",
                        rate_limit_key=f"{RATE_LIMIT_KEY_PREFIX}down:{node['wg_address']}",
                        rate_limit_seconds=RATE_LIMIT_SECONDS,
                    )

            for replica in health.get("replicas", []):
                lag = replica.get("lag_bytes")
                if lag is not None:
                    if lag > 10 * 1024 * 1024:  # 10MB
                        logger.critical(
                            f"CRITICAL replication lag on {replica['name']}: "
                            f"{lag / 1024 / 1024:.1f}MB"
                        )
                        _dispatch_replication_alert(
                            mesh, replica,
                            event_type="replication_lag",
                            lag_bytes=lag,
                            rate_limit_key=f"{RATE_LIMIT_KEY_PREFIX}lag:{replica['wg_address']}",
                            rate_limit_seconds=RATE_LIMIT_SECONDS,
                        )
                    elif lag > 1024 * 1024:  # 1MB
                        logger.warning(
                            f"Replication lag on {replica['name']}: "
                            f"{lag / 1024:.0f}KB"
                        )

            # C3: Bridge Patroni → ElectionService.
            # If Patroni says there's no leader but our Raft state thinks there is,
            # force an election re-check so stale leaders are demoted.
            patroni_leader = health.get("patroni_leader")
            if patroni_leader is None or patroni_leader == "":
                try:
                    from apps.deployments.services.election_service import ElectionService
                    cluster = ElectionService.get_or_create_cluster(mesh=mesh)
                    if cluster.leader_server is not None:
                        logger.warning(
                            "C3: Patroni reports no leader but election leader "
                            "is %s — triggering re-election check",
                            cluster.leader_server,
                        )
                        cluster.state = "ELECTION_NEEDED"
                        cluster.save(update_fields=["state"])
                except Exception as exc:
                    logger.error("C3: Failed to bridge Patroni→Election: %s", exc)

        except Exception as e:
            logger.error(f"Replication health check failed for mesh {mesh.name}: {e}")


def _dispatch_replication_alert(mesh, node, *, event_type, lag_bytes=None,
                                rate_limit_key, rate_limit_seconds):
    """Dispatch a replication notification with per-node rate limiting."""
    from django.core.cache import cache

    from apps.deployments.models.servers import ManagedServer
    from apps.notifications.tasks import notify_replication_issue

    if cache.get(rate_limit_key):
        return  # Already alerted recently

    wg_address = node.get('wg_address', '')
    server = ManagedServer.objects.filter(wg_address=wg_address).first()
    if not server or not server.owner_id:
        return

    notify_replication_issue.delay(
        user_id=server.owner_id,
        event_type=event_type,
        mesh_name=mesh.name,
        node_name=node.get('name', wg_address),
        wg_address=wg_address,
        lag_bytes=lag_bytes,
    )
    cache.set(rate_limit_key, "1", timeout=rate_limit_seconds)


@shared_task(
    bind=True,
    name="apps.deployments.tasks_replication.deploy_replication_task",
    max_retries=0,
    soft_time_limit=1200,
    time_limit=1260)
def deploy_replication_task(self, mesh_id: str, db_password: str,
                             admin_password: str,
                             replication_password: str = "repl_pass"):
    """
    Deploy Patroni replication cluster to a mesh (async).

    SEC-ZT-006: Passwords should be pre-encrypted with encrypt_arg()
    before being passed to delay(). If they start with 'enc:', they
    will be decrypted transparently.
    """
    # Decrypt arguments if they were encrypted (SEC-ZT-006)
    db_password = decrypt_arg(db_password)
    admin_password = decrypt_arg(admin_password)
    replication_password = decrypt_arg(replication_password)

    if not all([db_password, admin_password]):
        logger.error("Missing required credentials for replication deploy (mesh %s)", mesh_id)
        return {"error": "Missing required credentials"}

    logger.info(
        "Deploying replication for mesh %s (passwords encrypted via SEC-ZT-006)",
        mesh_id,
    )
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.replication_service import ReplicationService

    lock_key = f"replication-deploy:{mesh_id}"
    if not cache.add(lock_key, "1", timeout=1260):
        logger.warning("Replication deploy skipped for %s because another deploy is running", mesh_id)
        return {"error": "Replication deployment already running"}

    try:
        mesh = MeshNetwork.objects.get(id=mesh_id)
        mesh.replication_status = "DEPLOYING"
        mesh.replication_last_error = ""
        mesh.replication_last_result = {}
        mesh.replication_updated_at = timezone.now()
        mesh.save(update_fields=[
            "replication_status",
            "replication_last_error",
            "replication_last_result",
            "replication_updated_at",
            "updated_at",
        ])
        results = ReplicationService.deploy_replication(
            mesh, db_password, admin_password, replication_password,
        )
        failed = [
            node for node in results.get("patroni", [])
            if str(node.get("status", "")).startswith("FAILED")
        ]
        haproxy_failed = str(results.get("haproxy", "")).startswith("FAILED")
        if failed or haproxy_failed:
            readiness = {
                "status": "SKIPPED",
                "reason": "deployment step failed",
            }
        else:
            readiness = ReplicationService.wait_for_cluster_ready(
                mesh,
                timeout_seconds=180,
                poll_seconds=5,
            )
        results["readiness"] = readiness
        readiness_failed = readiness.get("status") != "READY"
        mesh.replication_status = "FAILED" if failed or haproxy_failed or readiness_failed else "ACTIVE"
        mesh.replication_last_error = "; ".join(
            [str(node.get("status")) for node in failed]
            + ([str(results.get("haproxy"))] if haproxy_failed else [])
            + (["Replication did not become ready before timeout."] if readiness_failed else [])
        )
        mesh.replication_last_result = results
        mesh.replication_updated_at = timezone.now()
        mesh.save(update_fields=[
            "replication_status",
            "replication_last_error",
            "replication_last_result",
            "replication_updated_at",
            "updated_at",
        ])
        logger.info("Replication deployment completed for mesh %s", mesh.name)
        return results
    except MeshNetwork.DoesNotExist:
        logger.error(f"Mesh {mesh_id} not found")
        return {"error": "Mesh not found"}
    except Exception as e:
        try:
            mesh.replication_status = "FAILED"
            mesh.replication_last_error = _bounded_error(e)
            mesh.replication_last_result = {"error": _bounded_error(e)}
            mesh.replication_updated_at = timezone.now()
            mesh.save(update_fields=[
                "replication_status",
                "replication_last_error",
                "replication_last_result",
                "replication_updated_at",
                "updated_at",
            ])
        except Exception:
            pass
        logger.error(f"Replication deployment failed: {e}")
        return {"error": _bounded_error(e)}
    finally:
        cache.delete(lock_key)


@shared_task(name="apps.deployments.tasks_replication.manual_failover_task")
def manual_failover_task(mesh_id: str, target_wg_address: str):
    """Trigger manual Patroni failover to a target replica (async)."""
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.replication_service import ReplicationService

    try:
        mesh = MeshNetwork.objects.get(id=mesh_id)
        result = ReplicationService.manual_failover(mesh, target_wg_address)
        logger.info(f"Failover result: {result}")
        return result
    except MeshNetwork.DoesNotExist:
        logger.error(f"Mesh {mesh_id} not found")
    except Exception as e:
        logger.error(f"Failover failed: {e}")
        return {"error": str(e)}
