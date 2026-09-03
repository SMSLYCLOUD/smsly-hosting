"""Service HA Manager — unified high-availability orchestration.

Two modes (per-service opt-in via Service.ha_mode):

  LOCAL HA ('local'): multiple container replicas on the SAME node.
    - The health monitor already auto-restarts unhealthy containers
    - This manager adds: container restart escalation, replica count
      maintenance (ensure N replicas are running), and alerting
    - Fast failover (<5s — Docker restart policy handles it)
    - Survives: container crashes, OOM kills, app panics
    - Does NOT survive: node-level failure (disk, kernel, power)

  REMOTE HA ('remote'): replica on a DIFFERENT node.
    - When the primary goes unhealthy past the restart window, or the
      node goes offline, spawn a replacement on the best alternative
    - Survives: node failure, disk loss, network partition, power
    - Slower failover (30-120s — needs image pull + container start)
    - DNS cutover follows the existing _update_service_a_record flow

Gated by PlatformConfig.service_ha_enabled (master toggle).
Runs as a beat task (every 60s) and is idempotent — each pass
re-evaluates the current state and takes at most one action per
service.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

# How long a service must be unhealthy before remote failover is considered
FAILOVER_UNHEALTHY_SECONDS = 120
# How long a node must be silent before it's considered offline
NODE_OFFLINE_SECONDS = 90
# Cooldown between remote failover attempts for the same service
FAILOVER_COOLDOWN_SECONDS = 300
# How long a local-replica service can run below its replica target
LOCAL_REPLICA_RECONCILE_SECONDS = 120


class ServiceHAManager:
    """Unified service high-availability orchestrator."""

    def __init__(self):
        from django.core.cache import cache
        self.cache = cache

    def run_ha_pass(self) -> dict:
        """Main entry point — one idempotent HA evaluation pass."""
        from apps.deployments.models import PlatformConfig

        # Master toggle — if disabled, no-op entirely
        config = PlatformConfig.load()
        if not getattr(config, 'service_ha_enabled', False):
            return {
                "status": "disabled",
                "checked": 0,
                "local_reconciles": 0,
                "remote_failovers": 0,
                "node_failovers": 0,
                "escalations": 0,
                "alerts": [],
            }

        results = {
            "status": "ok",
            "checked": 0,
            "local_reconciles": 0,
            "remote_failovers": 0,
            "node_failovers": 0,
            "escalations": 0,
            "alerts": [],
        }

        # ── Layer 3: Node failure detection (highest priority) ──────────
        node_results = self._check_node_failures()
        results["node_failovers"] = node_results.get("failovers", 0)
        results["alerts"].extend(node_results.get("alerts", []))

        # ── Layer 2: Remote replica failover for unhealthy services ─────
        remote_results = self._check_remote_failovers()
        results["remote_failovers"] = remote_results.get("failovers", 0)

        # ── Local HA: ensure replica count matches min_replicas ────────
        local_results = self._reconcile_local_replicas()
        results["local_reconciles"] = local_results.get("reconciled", 0)

        # ── Layer 1: Escalation alerts ────────────────────────────────
        escalation_results = self._check_escalations()
        results["escalations"] = escalation_results.get("escalated", 0)
        results["checked"] = escalation_results.get("checked", 0)

        return results

    # ── LOCAL HA: maintain replica count on the same node ────────────
    def _reconcile_local_replicas(self) -> dict:
        """For services with ha_mode='local', ensure the number of running
        containers on the node matches service.min_replicas. If a replica
        died, restart it. If the primary container died, the health
        monitor already handles it — this only manages the REPLICA count.

        Local HA is simple: containers have restart_policy=unless-stopped,
        so Docker restarts them automatically. This reconciler catches
        containers that exceeded Docker's restart count (or were manually
        stopped) and re-creates them from the known-good image.
        """
        from apps.deployments.models import Service

        reconciled = 0

        services = Service.objects.filter(
            status=Service.Status.ACTIVE,
            ha_mode='local',
            auto_restart=True,
        )

        for svc in services:
            # Cooldown per service
            key = f"ha:local_reconcile:{svc.id}"
            if self.cache.get(key):
                continue
            self.cache.set(key, True, timeout=LOCAL_REPLICA_RECONCILE_SECONDS)

            target_replicas = max(1, svc.min_replicas or 1)

            # Count running containers for this service (by label)
            running = self._count_running_containers(svc)
            if running < target_replicas:
                logger.info(
                    "HA local: %s has %d/%d replicas running — spawning replacement",
                    svc.name, running, target_replicas,
                )
                if self._spawn_local_replica(svc, target_replicas - running):
                    reconciled += 1

        return {"reconciled": reconciled}

    def _count_running_containers(self, service) -> int:
        """Count running containers by smsly.service_id label."""
        try:
            from apps.cloud.docker_client import get_docker_client
            client = get_docker_client()
            containers = client.containers.list(
                filters={
                    'label': f'smsly.service_id={service.id}',
                    'status': 'running',
                },
            )
            return len(containers)
        except Exception as exc:
            logger.debug("HA: container count failed for %s: %s", service.name, exc)
            return 0

    def _spawn_local_replica(self, service, count_needed: int) -> bool:
        """Spawn a replacement replica container on the same node.

        Uses the service's current docker_image + env vars. The existing
        _deploy_container task handles the full pipeline (env, health
        check, Traefik labels). We just queue a new deployment that
        will create a container.
        """
        try:
            from apps.deployments.models import Deployment

            # Don't spawn if there's already an in-flight deployment
            in_flight = Deployment.objects.filter(
                service=service,
                status__in=[
                    Deployment.Status.QUEUED,
                    Deployment.Status.BUILDING,
                    Deployment.Status.DEPLOYING,
                    Deployment.Status.HEALTH_CHECK,
                ],
            ).exists()
            if in_flight:
                return False

            # Only spawn if we have a known-good image
            if not str(service.docker_image or '').strip():
                logger.debug("HA local: %s has no docker_image — skipping", service.name)
                return False

            from apps.deployments.tasks.deploy.helpers import enqueue_smart_deploy_task
            from apps.cloud.models import CloudProvider

            provider = CloudProvider.objects.filter(
                provider_type=CloudProvider.ProviderType.LOCAL,
            ).first()
            if not provider:
                return False

            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash='latest',
                commit_message=f'HA local replica reconcile ({count_needed} needed)',
            )

            enqueue_smart_deploy_task(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
                skip_review=True,
            )
            return True

        except Exception as exc:
            logger.error("HA local replica spawn failed for %s: %s", service.name, exc)
            return False

    # ── REMOTE HA: replica failover to a different node ────────────────
    def _check_remote_failovers(self) -> dict:
        """For services with ha_mode='remote' that are unhealthy past
        the restart window, spawn a replacement on an alternative node."""
        from apps.deployments.models import Service

        failovers = 0
        cutoff = timezone.now() - timedelta(seconds=FAILOVER_UNHEALTHY_SECONDS)

        candidates = Service.objects.filter(
            status=Service.Status.ACTIVE,
            ha_mode='remote',
            auto_restart=True,
            health_status='unhealthy',
        ).exclude(
            health_status='needs_manual_intervention',
        )

        for svc in candidates:
            cooldown_key = f"ha:remote_failover:{svc.id}"
            if self.cache.get(cooldown_key):
                continue

            # Verify the service has been unhealthy long enough
            unhealthy_since = getattr(svc, 'updated_at', None)
            if not unhealthy_since or unhealthy_since > cutoff:
                continue

            target_node = self._find_failover_target(svc)
            if not target_node:
                logger.info("HA remote: no failover target for %s", svc.name)
                continue

            if self._spawn_remote_replica(svc, target_node):
                failovers += 1
                self.cache.set(cooldown_key, True, timeout=FAILOVER_COOLDOWN_SECONDS)
                logger.warning(
                    "HA remote: spawned failover replica for %s on node %s",
                    svc.name, target_node.name,
                )

        return {"failovers": failovers}

    def _find_failover_target(self, service):
        """Find the best node to failover a service to."""
        from apps.deployments.models import ManagedServer

        current_node = getattr(service, 'server', None)

        candidates = ManagedServer.objects.filter(
            status='ONLINE',
        ).exclude(
            id=current_node.id if current_node else None,
        ).order_by('?')  # random for load spreading

        for node in candidates:
            if self._node_is_responsive(node):
                return node

        return None

    def _node_is_responsive(self, node) -> bool:
        """Quick responsiveness check — recent heartbeat or successful probe."""
        import socket

        # Check for recent heartbeat in cache
        hb_key = f"node:heartbeat:{node.id}"
        if self.cache.get(hb_key):
            return True

        # Try a TCP probe on the node's agent port
        try:
            host = node.host or node.private_ip or node.wg_address
            if host:
                if '/' in str(host):
                    host = str(host).split('/')[0]
                sock = socket.create_connection((host, 8000), timeout=5)
                sock.close()
                return True
        except (socket.gaierror, socket.timeout, OSError):
            pass

        return False

    def _spawn_remote_replica(self, service, target_node) -> bool:
        """Spawn a replacement replica on the target node."""
        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.models import Deployment

        try:
            # Don't spawn if there's already an in-flight deployment
            in_flight = Deployment.objects.filter(
                service=service,
                status__in=[
                    Deployment.Status.QUEUED,
                    Deployment.Status.BUILDING,
                    Deployment.Status.DEPLOYING,
                    Deployment.Status.HEALTH_CHECK,
                ],
            ).exists()
            if in_flight:
                return False

            # Don't spawn if we already have a running replica on this node
            existing = ServiceReplica.objects.filter(
                service=service,
                node=target_node,
                status='RUNNING',
            ).exists()
            if existing:
                return False

            replica = ServiceReplica.objects.create(
                service=service,
                node=target_node,
                container_name=f"{service.name}-ha-{target_node.node_number or 1}",
                status='SPAWNING',
                spawn_reason=(
                    f"HA remote failover (unhealthy since {service.updated_at})"
                ),
            )

            from apps.deployments.tasks.deploy.helpers import enqueue_smart_deploy_task
            from apps.cloud.models import CloudProvider

            provider = CloudProvider.objects.filter(
                provider_type=CloudProvider.ProviderType.LOCAL,
            ).first()
            if not provider:
                replica.status = 'DESTROYED'
                replica.save(update_fields=['status'])
                return False

            deployment = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash='latest',
                commit_message=f'HA remote failover to {target_node.name}',
            )
            deployment.target_server = target_node
            deployment.save(update_fields=['target_server'])

            enqueue_smart_deploy_task(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
                skip_review=True,
            )

            replica.container_id = str(deployment.id)
            replica.status = 'RUNNING'
            replica.save(update_fields=['container_id', 'status'])
            return True

        except Exception as exc:
            logger.error("HA remote failover spawn failed for %s: %s", service.name, exc)
            return False

    # ── Node failure detection + service respawn ────────────────────────
    def _check_node_failures(self) -> dict:
        """Detect offline nodes and respawn their remote-HA services elsewhere."""
        from apps.deployments.models import ManagedServer, Service

        failovers = 0
        alerts = []
        cutoff = timezone.now() - timedelta(seconds=NODE_OFFLINE_SECONDS)

        offline_nodes = ManagedServer.objects.filter(
            status='ONLINE',
            updated_at__lt=cutoff,
        ).exclude(
            is_primary=True,
        )

        for node in offline_nodes:
            if self._node_is_responsive(node):
                continue

            # Only respawn services that opted into remote HA
            affected = Service.objects.filter(
                server=node,
                status=Service.Status.ACTIVE,
                ha_mode='remote',
            )

            for svc in affected:
                cooldown_key = f"ha:node_failover:{svc.id}:{node.id}"
                if self.cache.get(cooldown_key):
                    continue

                target = self._find_failover_target(svc)
                if target and target.id != node.id:
                    if self._spawn_remote_replica(svc, target):
                        failovers += 1
                        self.cache.set(cooldown_key, True, timeout=600)
                        alerts.append(
                            f"Node {node.name} offline — {svc.name} "
                            f"failed over to {target.name}"
                        )

            if affected.exists():
                node_alert_key = f"ha:node_offline_alert:{node.id}"
                if not self.cache.get(node_alert_key):
                    self.cache.set(node_alert_key, True, timeout=3600)
                    alerts.append(
                        f"Node {node.name} has been offline for >"
                        f"{NODE_OFFLINE_SECONDS}s "
                        f"({affected.count()} HA services affected)"
                    )

        return {"failovers": failovers, "alerts": alerts}

    # ── Escalation alerts ─────────────────────────────────────────────
    def _check_escalations(self) -> dict:
        """Send periodic reminders for services stuck in manual intervention."""
        from apps.deployments.models import Service

        escalated = 0
        checked = 0

        services = Service.objects.filter(
            status=Service.Status.ACTIVE,
            auto_restart=True,
            health_status='needs_manual_intervention',
        ).exclude(
            ha_mode='none',
        )

        for svc in services:
            checked += 1
            key = f"ha:escalation_reminder:{svc.id}"
            if self.cache.get(key):
                continue
            self.cache.set(key, True, timeout=1800)

            try:
                from apps.core.tasks.alerts import alert_user_task
                latest = svc.deployments.order_by('-created_at').first()
                if latest:
                    alert_user_task.delay(
                        deployment_id=str(latest.id),
                        error_message=(
                            f"HA ESCALATION: {svc.name} (mode={svc.ha_mode}) "
                            f"has been in 'needs_manual_intervention' state. "
                            f"Auto-restart and auto-rollback have been exhausted. "
                            f"Manual intervention required."
                        ),
                    )
                escalated += 1
            except Exception as exc:
                logger.debug("HA escalation alert failed for %s: %s", svc.name, exc)

        return {"escalated": escalated, "checked": checked}
