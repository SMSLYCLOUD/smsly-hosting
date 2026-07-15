import logging
from typing import Any

import docker
from django.utils import timezone

from apps.cloud.docker_client import get_docker_client
from apps.deployments.models_addons import Addon
from apps.deployments.models_core import Deployment, Service

logger = logging.getLogger(__name__)

class DeletionOrchestrator:
    """
    Central orchestrator for reliable runtime resource cleanup.
    """

    def __init__(self):
        try:
            self.docker_client = get_docker_client()
        except Exception as e:
            logger.error(f"DeletionOrchestrator failed to initialize docker client: {e}")
            self.docker_client = None

    def delete_service_resources(self, service: Service, force: bool = False) -> bool:
        """
        Deletes all runtime resources associated with a Service.
        If force=True, we return True even if some resource removals fail,
        allowing the DB record to be purged.
        """
        if not self.docker_client:
            # If no docker client, we can only succeed if forced or if it's a metadata-only service
            return force or not service.server

        success = True
        try:
            # 1. Stop and remove containers
            containers_to_remove = self._find_service_containers(service)
            for container in containers_to_remove:
                if not self._safe_remove_container(container):
                    success = False

            # 3. Cleanup volumes
            volumes_to_remove = self._find_service_volumes(service)
            for volume in volumes_to_remove:
                if not self._safe_remove_volume(volume):
                    success = False

            # 4. Clean up tunnels/FRP if any exist
            try:
                self._cleanup_tunnels(service)
            except Exception as e:
                logger.warning(f"Tunnel cleanup failed for {service.id}: {e}")
                if not force:
                    success = False

            # 5. Cancel Celery tasks related to this service
            try:
                self._cancel_deployments(service)
            except Exception as e:
                logger.warning(f"Deployment cancellation failed for {service.id}: {e}")

        except Exception as e:
            logger.error(f"Error during service deletion orchestration for {service.id}: {e}")
            success = False

        return force or success

    def delete_addon_resources(self, addon: Addon) -> bool:
        """
        Deletes all runtime resources associated with an Addon.
        """
        if not self.docker_client:
            return False

        success = True
        try:
            containers_to_remove = self._find_addon_containers(addon)
            for container in containers_to_remove:
                if not self._safe_remove_container(container):
                    success = False

            # Remove persistent volumes for addons
            volumes_to_remove = self._find_addon_volumes(addon)
            for volume in volumes_to_remove:
                if not self._safe_remove_volume(volume):
                    success = False

        except Exception as e:
            logger.error(f"Error during addon deletion orchestration for {addon.id}: {e}")
            success = False

        return success

    def cleanup_orphaned_resources(self, dry_run=True):
        pass

    def purge_user_backup_artifacts(self, user_id) -> dict:
        """
        GDPR right-to-erasure helper. Delegates to the backup service so the
        deletion orchestrator owns the single import path; this wrapper exists
        for callers that already hold an orchestrator instance.
        """
        from apps.deployments.services.backup_service import purge_user_backups
        return purge_user_backups(user_id)

    # -- Internal Discovery Methods --

    def _find_service_containers(self, service: Service) -> set[Any]:
        containers: set[Any] = set()
        if not self.docker_client:
            return containers

        try:
            runtime_id = str(getattr(service, 'active_runtime_id', '') or '').strip()
            if runtime_id:
                try:
                    containers.add(self.docker_client.containers.get(runtime_id))
                except docker.errors.NotFound:
                    pass
                except Exception as e:
                    logger.warning(
                        "Failed to inspect active runtime %s for service %s: %s",
                        runtime_id, service.id, e,
                    )

            all_containers = self.docker_client.containers.list(all=True)
            for c in all_containers:
                # 1. Check labels
                labels = c.labels
                if labels.get('smsly.service_id') == str(service.id):
                    containers.add(c)
                    continue

                canonical = str(labels.get('smsly.blue_green.canonical_name') or '').lower()
                if canonical and canonical in {
                    str(service.name or '').lower(),
                    str(getattr(service, 'slug', '') or '').lower(),
                }:
                    containers.add(c)
                    continue

                # 2. Check legacy name patterns
                c_name = c.name.lower()
                slug_lower = service.slug.lower() if hasattr(service, 'slug') else service.name.lower()

                # Match containers whose name STARTS with the slug followed
                # by a separator (-, _).  A bare substring match would catch
                # unrelated containers (e.g. slug='app' matching 'flux-app').
                if c_name == slug_lower or c_name.startswith(slug_lower + '-') or c_name.startswith(slug_lower + '_'):
                    containers.add(c)
                    continue

                # 3. Check compose project labels
                if labels.get('com.docker.compose.project') == slug_lower:
                    containers.add(c)
                    continue

        except Exception as e:
            logger.warning(f"Failed to list containers for service {service.id}: {e}")

        return containers

    def _find_addon_containers(self, addon: Addon) -> set[Any]:
        containers: set[Any] = set()
        if not self.docker_client:
            return containers

        try:
            all_containers = self.docker_client.containers.list(all=True)
            for c in all_containers:
                # 1. Check labels
                labels = c.labels
                if labels.get('smsly.addon_id') == str(addon.id):
                    containers.add(c)
                    continue

                # 2. Check legacy name pattern (container name starts with smsly-addon-)
                addon_prefix = "smsly-addon-"
                if c.name.lower().startswith(addon_prefix) and str(addon.id)[:8] in c.name:
                    containers.add(c)
                    continue

                # Addon name fallback
                addon_type = addon.addon_type.lower()
                if c.name == f"smsly-addon-{addon_type}-{addon.id}":
                    containers.add(c)

        except Exception as e:
            logger.warning(f"Failed to list containers for addon {addon.id}: {e}")

        return containers

    def _find_service_volumes(self, service: Service) -> set[Any]:
        volumes: set[Any] = set()
        if not self.docker_client:
            return volumes
        try:
            # We don't forcefully delete all matching volumes for services,
            # only those explicitly labeled or explicitly managed
            slug_lower = service.slug.lower() if hasattr(service, 'slug') else service.name.lower()
            all_vols = self.docker_client.volumes.list()
            for v in all_vols:
                labels = v.attrs.get('Labels') or {}
                if labels.get('smsly.service_id') == str(service.id) or labels.get('com.docker.compose.project') == slug_lower:
                    volumes.add(v)
        except Exception as e:
            logger.warning(f"Failed to list volumes for service {service.id}: {e}")
        return volumes

    def _find_addon_volumes(self, addon: Addon) -> set[Any]:
        volumes: set[Any] = set()
        if not self.docker_client:
            return volumes
        try:
            all_vols = self.docker_client.volumes.list()
            expected_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}-data"
            for v in all_vols:
                labels = v.attrs.get('Labels') or {}
                if labels.get('smsly.addon_id') == str(addon.id) or v.name == expected_name:
                    volumes.add(v)
        except Exception as e:
            logger.warning(f"Failed to list volumes for addon {addon.id}: {e}")
        return volumes

    # -- Action Methods --

    def _safe_remove_container(self, container) -> bool:
        try:
            logger.info(f"Stopping container {container.name} ({container.id})")
            container.stop(timeout=10)
        except docker.errors.NotFound:
            return True
        except Exception as e:
            logger.warning(f"Failed to stop container {container.name}: {e}")

        try:
            logger.info(f"Removing container {container.name} ({container.id})")
            container.remove(force=True)
            return True
        except docker.errors.NotFound:
            return True
        except Exception as e:
            logger.error(f"Failed to remove container {container.name}: {e}")
            return False

    def _safe_remove_volume(self, volume) -> bool:
        try:
            logger.info(f"Removing volume {volume.name}")
            volume.remove(force=True)
            return True
        except docker.errors.NotFound:
            return True
        except docker.errors.APIError as e:
            logger.error(f"Failed to remove volume {volume.name} (API Error): {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to remove volume {volume.name}: {e}")
            return False

    def _cleanup_tunnels(self, service: Service):
        pass

    def _cancel_deployments(self, service: Service):
        Deployment.objects.filter(
            service=service,
            status__in=[
                Deployment.Status.ACTIVE,
                Deployment.Status.BUILDING,
                Deployment.Status.DEPLOYING,
                Deployment.Status.QUEUED,
                Deployment.Status.REVIEW,
            ],
        ).update(status=Deployment.Status.CANCELLED, finished_at=timezone.now())
