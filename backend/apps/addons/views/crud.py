"""Views Addons module."""
from __future__ import annotations

import logging
import re

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.teams.permissions import (
    assert_can_delete,
    assert_can_write,
    get_team_q_filter,
)

from ..models import Addon
from apps.deployments.models import Service

logger = logging.getLogger(__name__)


def _guard_delay(task_func, *args, **kwargs):
    """Queue a Celery task, returning (ok, task_id).

    If the broker is unreachable the task is NOT queued. Callers must return
    a 503-style response instead of crashing with an unhandled 500.
    """
    try:
        task = task_func.delay(*args, **kwargs)
        return True, getattr(task, 'id', None)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.error(
            "Failed to queue Celery task %s",
            getattr(task_func, 'name', repr(task_func)),
            exc_info=True,
        )
        return False, None


class _ClosingFileResponse(FileResponse):
    """FileResponse that explicitly closes its underlying file when closed.

    Django's FileResponse does register the file in ``_closable_objects``,
    but if an exception interrupts the normal close path the file can leak.
    We store a reference as ``self._file`` and close it defensively in
    ``close()`` so the OS file descriptor is released as soon as the response
    finishes.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Surface the wrapped file as a public-ish attribute so callers (and
        # tests) can introspect / force-close it. FileResponse uses
        # ``file_to_stream`` internally; mirror it as ``_file`` for clarity.
        self._file = getattr(self, "file_to_stream", None) or getattr(self, "_file", None)

    def close(self):
        try:
            return super().close()
        finally:
            f = getattr(self, '_file', None)
            if f is not None and hasattr(f, 'close') and not getattr(f, 'closed', True):
                try:
                    f.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.debug("Failed to close file handle", exc_info=True)


class AddonSerializer(serializers.ModelSerializer):
    server = serializers.ReadOnlyField(source='service.server_id')

    class Meta:
        model = Addon
        fields = [
            'id',
            'service',
            'name',
            'addon_type',
            'status',
            'server',
            'public_domain',
            'is_bucket_public',
            'created_at']
        read_only_fields = ['status', 'connection_url', 'created_at']


class BackupSerializer(serializers.ModelSerializer):
    class Meta:
        from ..models import Backup as BackupModel
        model = BackupModel
        fields = ['id', 'addon', 'status', 'size_bytes', 'created_at', 'completed_at', 'error_message']
        read_only_fields = ['status', 'size_bytes', 'created_at', 'completed_at', 'error_message']


class AddonViewSet(viewsets.ModelViewSet):
    queryset = Addon.objects.all().order_by('id')
    serializer_class = AddonSerializer
    permission_classes = [IsAuthenticated]

    # ==========================================================================
    # SECURITY: Zero Trust - Only return addons for user's own services
    # ==========================================================================
    def get_queryset(self):
        """Filter addons to only those belonging to the user's accessible services."""
        allowed_services = Service.objects.filter(get_team_q_filter(self.request.user))
        qs = self.queryset.select_related('service').filter(
            Q(service__in=allowed_services)
        )
        # Superusers can see ownerless (orphaned) addons for cleanup.
        if self.request.user.is_superuser:
            qs = qs | self.queryset.filter(service__owner__isnull=True)
        project_id = self.request.query_params.get('project_id')
        if project_id:
            qs = qs.filter(service__project_id=project_id)
        return qs.distinct()

    def perform_create(self, serializer):
        # SECURITY: Verify user has access to the service before creating addon
        service = serializer.validated_data.get('service')
        if service:
            assert_can_write(self.request.user, service, action='create addon')

        addon = serializer.save()

        # Auto-expose new addons by default
        if not addon.public_domain:
            from apps.deployments.models import Service
            base_domain = Service.default_public_base_domain()
            short_id = str(addon.id).split('-')[0]
            slug = re.sub(r'[^a-z0-9]+', '-', addon.addon_type.lower()).strip('-')
            addon.public_domain = f"addon-{slug}-{short_id}.{base_domain}"
            addon.save(update_fields=['public_domain'])

        # Trigger async provisioning via Celery (uses Docker-native
        # provisioner)
        from ..tasks.crud import provision_addon_task
        ok, _ = _guard_delay(provision_addon_task, addon_id=str(addon.id))
        if not ok:
            raise serializers.ValidationError(
                "Addon created, but provisioning could not be queued. "
                "Use 'reprovision' to retry."
            )


    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        assert_can_delete(self.request.user, instance.service)
        self.perform_destroy(instance)
        return Response(
            {
                "status": "deletion_pending",
                "message": "Deletion has started.",
                "resource_id": str(instance.id),
            },
            status=status.HTTP_202_ACCEPTED
        )

    def perform_destroy(self, instance):
        """Set status to pending and queue async deletion."""
        from ..models import Addon
        from apps.deployments.tasks.deployment.tasks_addons import delete_addon_task

        instance.status = Addon.Status.DELETION_PENDING
        instance.save(update_fields=['status'])

        ok, _ = _guard_delay(delete_addon_task, str(instance.id))
        if not ok:
            instance.status = Addon.Status.DELETION_FAILED
            instance.deletion_error = "Broker unavailable; deletion could not be queued"
            instance.save(update_fields=['status', 'deletion_error'])
            raise serializers.ValidationError(
                "Deletion could not be queued. Retry using 'retry-delete'."
            )

    @action(detail=True, methods=['post'], url_path='enable-ha')
    def enable_ha(self, request, pk=None):
        """Enable automatic-failover HA for a Redis addon (Phase 1 scope)."""
        instance = self.get_object()
        assert_can_write(request.user, instance.service, action='enable addon HA')

        if instance.ha_enabled:
            return Response(
                {'error': 'HA is already enabled for this addon.'},
                status=status.HTTP_409_CONFLICT)
        if instance.addon_type != Addon.Type.REDIS:
            return Response(
                {'error': 'HA is currently supported for REDIS addons only.'},
                status=status.HTTP_400_BAD_REQUEST)
        if instance.status != Addon.Status.ACTIVE:
            return Response(
                {'error': f"Addon must be ACTIVE to enable HA (current: {instance.status})."},
                status=status.HTTP_409_CONFLICT)

        creds = instance.parsed_credentials
        password = creds.get('REDIS_PASSWORD') or ''
        if not password:
            return Response(
                {'error': 'Could not resolve addon password from connection_url.'},
                status=status.HTTP_409_CONFLICT)

        from ..services.addon_ha import AddonHaError, AddonHaManager
        from apps.addons.services.addon_provisioner import addon_provisioner

        manager = AddonHaManager(network_name=addon_provisioner.network_name)
        instance.ha_status = Addon.HaStatus.ENABLING
        instance.save(update_fields=['ha_status'])
        try:
            topology = manager.enable_redis_ha(instance, password)
        except AddonHaError as exc:
            instance.ha_status = Addon.HaStatus.FAILED
            instance.save(update_fields=['ha_status'])
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        instance.ha_enabled = True
        instance.ha_status = Addon.HaStatus.HEALTHY
        instance.replica_container_name = topology['replica']
        instance.ha_topology = topology
        instance.save(update_fields=[
            'ha_enabled', 'ha_status', 'replica_container_name', 'ha_topology',
        ])
        return Response({
            'status': 'healthy',
            'mode': topology['mode'],
            'topology': topology,
            'note': 'Failover is automatic (Sentinel quorum=2). The connection URL is unchanged.',
        })

    @action(detail=True, methods=['post'], url_path='disable-ha')
    def disable_ha(self, request, pk=None):
        """Remove HA components and restore the alias onto the live master."""
        instance = self.get_object()
        assert_can_write(request.user, instance.service, action='disable addon HA')

        if not instance.ha_enabled:
            return Response(
                {'error': 'HA is not enabled for this addon.'},
                status=status.HTTP_409_CONFLICT)

        from ..services.addon_ha import AddonHaManager
        from apps.addons.services.addon_provisioner import addon_provisioner

        manager = AddonHaManager(network_name=addon_provisioner.network_name)
        removed = manager.teardown(instance)
        instance.ha_enabled = False
        instance.ha_status = Addon.HaStatus.DISABLED
        instance.replica_container_name = ''
        instance.ha_topology = {}
        instance.save(update_fields=[
            'ha_enabled', 'ha_status', 'replica_container_name', 'ha_topology',
        ])
        return Response({'status': 'disabled', 'removed': removed})

    @action(detail=True, methods=['get'], url_path='ha-status')
    def ha_status(self, request, pk=None):
        """Report the HA topology and which container currently owns the master role."""
        instance = self.get_object()
        payload = {
            'ha_enabled': instance.ha_enabled,
            'ha_status': instance.ha_status,
            'mode': instance.ha_mode,
            'topology': instance.ha_topology,
            'master_container': None,
        }
        if instance.ha_enabled and instance.addon_type == Addon.Type.REDIS:
            from ..services.addon_ha import AddonHaManager
            manager = AddonHaManager(network_name='')
            payload['master_container'] = manager._current_master_container(instance)
        return Response(payload)

    @action(detail=True, methods=['post'], url_path='retry-delete')
    def retry_delete(self, request, pk=None):
        instance = self.get_object()
        assert_can_delete(self.request.user, instance.service)
        from ..models import Addon
        if instance.status not in [Addon.Status.DELETION_FAILED, Addon.Status.DELETION_PENDING]:
            return Response({"error": "Addon is not in a failed or pending deletion state."}, status=status.HTTP_400_BAD_REQUEST)

        instance.status = Addon.Status.DELETION_PENDING
        instance.save(update_fields=['status'])
        from ..tasks.crud import delete_addon_task
        ok, _ = _guard_delay(delete_addon_task, str(instance.id))
        if not ok:
            instance.status = Addon.Status.DELETION_FAILED
            instance.deletion_error = "Broker unavailable; deletion could not be queued"
            instance.save(update_fields=['status', 'deletion_error'])
            return Response(
                {"error": "Deletion could not be queued. Try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({"message": "Retry cleanup initiated."}, status=status.HTTP_202_ACCEPTED)

    def perform_update(self, serializer):
        # Allow updating properties like public_domain
        service = serializer.instance.service
        if service:
            assert_can_write(self.request.user, service, action='update addon')

        addon = serializer.save()
        # If public_domain changed, re-provision to update proxy labels
        if 'public_domain' in serializer.validated_data:
            from ..tasks.crud import provision_addon_task
            ok, _ = _guard_delay(provision_addon_task, str(addon.id))
            if not ok:
                raise serializers.ValidationError(
                    "Domain saved, but re-provisioning could not be queued. "
                    "Use 'reprovision' to retry."
                )

    @action(detail=True, methods=['post'])
    def expose(self, request, pk=None):
        """Auto-generate and assign a public domain for this addon."""
        addon = self.get_object()
        assert_can_write(self.request.user, addon.service, action='expose addon')
        from apps.deployments.models import Service
        base_domain = Service.default_public_base_domain()

        # Format: minio-uuid.domain.com
        short_id = str(addon.id).split('-')[0]
        slug = re.sub(r'[^a-z0-9]+', '-', addon.addon_type.lower()).strip('-')
        generated_domain = f"addon-{slug}-{short_id}.{base_domain}"

        addon.public_domain = generated_domain
        addon.save(update_fields=['public_domain'])

        from ..tasks.crud import provision_addon_task
        ok, _ = _guard_delay(provision_addon_task, str(addon.id))
        if not ok:
            return Response(
                {"error": "Domain saved, but provisioning could not be queued. Use 'reprovision' to retry."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({'public_domain': generated_domain})

    @action(detail=True, methods=['post'])
    def reprovision(self, request, pk=None):
        """Manually trigger re-provisioning to update labels or network configuration."""
        addon = self.get_object()
        assert_can_write(self.request.user, addon.service, action='reprovision addon')
        from ..tasks.crud import provision_addon_task
        ok, _ = _guard_delay(provision_addon_task, str(addon.id))
        if not ok:
            return Response(
                {"error": "Re-provisioning could not be queued. Try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({'status': 'reprovision_started'})

    @action(detail=True, methods=['post'])
    def deprovision(self, request, pk=None):
        """Delete addon container and remove from service."""
        addon = self.get_object()
        assert_can_delete(self.request.user, addon.service)
        from ..tasks.crud import deprovision_addon_task
        ok, _ = _guard_delay(deprovision_addon_task, addon_id=str(addon.id))
        if not ok:
            return Response(
                {"error": "Deprovisioning could not be queued. Try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({'status': 'deprovisioning'},
                        status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def credentials(self, request, pk=None):
        """Return parsed connection credentials for this addon."""
        addon = self.get_object()
        if addon.status != 'ACTIVE':
            return Response(
                {'error': 'Addon not active'},
                status=status.HTTP_400_BAD_REQUEST)
        return Response(addon.parsed_credentials)

    @action(detail=True, methods=['get'])
    def status_check(self, request, pk=None):
        """Check current addon container status."""
        addon = self.get_object()

        container_id = addon.coolify_uuid  # We store container_id here

        if not container_id:
            return Response({
                'status': addon.status,
                'message': 'Not yet provisioned'
            })

        # Check Docker container status
        from apps.addons.services.addon_provisioner import addon_provisioner

        try:
            container_status = addon_provisioner.get_status(container_id)
            return Response({
                'status': addon.status,
                'container_running': container_status.get('running', False),
                'container_status': container_status.get('status', 'unknown'),
            })
        except Exception as e:
            logger.error(f"Failed to check addon status: {e}")
            return Response({
                'status': addon.status,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        """
        Get runtime logs from the addon container.
        GET /api/v1/addons/{id}/logs/?tail=200
        """
        addon = self.get_object()
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"

        tail = int(request.query_params.get('tail', 200))
        tail = min(tail, 2000)

        from apps.addons.services.addon_provisioner import addon_provisioner

        try:
            log_text = addon_provisioner.get_logs(container_name, tail=tail)
            return Response({
                'id': str(addon.id),
                'addon_type': addon.addon_type,
                'container_name': container_name,
                'status': addon.status,
                'logs': log_text,
            })
        except Exception as e:
            logger.error("Failed to fetch addon logs for %s: %s", pk, e)
            return Response({
                'id': str(addon.id),
                'logs': '',
                'message': f'Could not fetch addon logs: {e!s}',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def network_check(self, request, pk=None):
        """
        Verify and repair network aliases for the addon container.
        GET /api/v1/addons/{id}/network_check/
        """
        addon = self.get_object()
        from apps.addons.services.addon_provisioner import addon_provisioner

        try:
            aliases = addon_provisioner.ensure_network_aliases(addon)
            return Response({
                'id': str(addon.id),
                'container_name': f"smsly-addon-{addon.addon_type.lower()}-{addon.id}",
                'aliases': aliases,
                'status': addon.status,
            })
        except Exception as e:
            logger.error("Network check failed for addon %s: %s", pk, e)
            return Response({
                'id': str(addon.id),
                'error': str(e),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def backup(self, request, pk=None):
        """Trigger a backup for this addon."""
        addon = self.get_object()
        assert_can_write(self.request.user, addon.service, action='backup addon')
        from ..tasks.crud import backup_addon_task
        ok, task_id = _guard_delay(backup_addon_task, addon_id=str(addon.id))
        if not ok:
            return Response(
                {"error": "Backup could not be queued. Try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({'status': 'backup_started', 'task_id': task_id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore a backup to this addon."""
        addon = self.get_object()
        assert_can_write(self.request.user, addon.service, action='restore addon')
        backup_id = request.data.get('backup_id')
        if not backup_id:
            return Response({'error': 'backup_id required'}, status=status.HTTP_400_BAD_REQUEST)

        # Verify backup belongs to addon
        from ..models import Backup
        if not Backup.objects.filter(id=backup_id, addon=addon).exists():
            return Response({'error': 'Backup not found for this addon'}, status=status.HTTP_404_NOT_FOUND)

        from ..tasks.crud import restore_addon_task
        ok, task_id = _guard_delay(restore_addon_task, backup_id=backup_id)
        if not ok:
            return Response(
                {"error": "Restore could not be queued. Try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({'status': 'restore_started', 'task_id': task_id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def backups(self, request, pk=None):
        """List backups for this addon."""
        addon = self.get_object()
        from ..models import Backup
        backups = Backup.objects.filter(addon=addon).order_by('-created_at')
        serializer = BackupSerializer(backups, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def download_backup(self, request, pk=None):
        """Download a backup file."""
        addon = self.get_object()
        backup_id = request.query_params.get('backup_id')
        if not backup_id:
            return Response({'error': 'backup_id required'}, status=status.HTTP_400_BAD_REQUEST)

        from ..models import Backup
        try:
            backup = Backup.objects.get(id=backup_id, addon=addon)
        except Backup.DoesNotExist:
            return Response({'error': 'Backup not found'}, status=status.HTTP_404_NOT_FOUND)

        import os
        if not os.path.exists(backup.file_path):
            return Response({'error': 'File not found on disk'}, status=status.HTTP_404_NOT_FOUND)

        # Security: ensure the file path is within the expected backups directory
        backups_root = os.path.realpath(os.path.join(settings.BASE_DIR, 'backups'))
        real_path = os.path.realpath(backup.file_path)
        if not real_path.startswith(backups_root):
            logger.warning("Blocked backup download path traversal: %s", backup.file_path)
            return Response({'error': 'Invalid backup path'}, status=status.HTTP_403_FORBIDDEN)

        backup_file = open(backup.file_path, 'rb')
        response = _ClosingFileResponse(
            backup_file,
            as_attachment=True,
            filename=os.path.basename(backup.file_path),
        )
        return response

    @action(detail=True, methods=['post'])
    def toggle_bucket_public(self, request, pk=None):
        """Toggle MinIO bucket public read access policy."""
        addon = self.get_object()
        assert_can_write(self.request.user, addon.service, action='toggle bucket access')

        if addon.addon_type != 'MINIO':
            return Response({'error': 'Only MinIO addons support public bucket toggling.'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.cloud.docker_client import get_docker_client
        is_public = request.data.get('is_public', False)

        # The container is always named smsly-addon-minio-<id>; the addon
        # ``name`` is only a network alias. Try the canonical name first,
        # then fall back to scanning for the addon UUID in container names
        # (mirrors toggle_bucket_public_api's UUID discovery).
        container_name = f"smsly-addon-minio-{addon.id}"
        bucket_name = "default-bucket"

        policy = "public" if is_public else "none"

        try:
            client = get_docker_client()
            # Robust container lookup (handles platform/compose prefixes)
            container = None
            try:
                container = client.containers.get(container_name)
            except Exception:
                addon_uuid = str(addon.id)
                possible = [
                    c for c in client.containers.list()
                    if addon_uuid.lower() in c.name.lower()
                ]
                if not possible:
                    possible = client.containers.list(filters={"name": container_name})
                if possible:
                    container = possible[0]
                else:
                    return Response({
                        'error': f'MinIO container not found: {container_name}'
                    }, status=status.HTTP_404_NOT_FOUND)

            # Execute the mc command inside the container
            cmd = ['mc', 'anonymous', 'set', policy, f'myminio/{bucket_name}']
            exit_code, output = container.exec_run(cmd)

            if exit_code != 0:
                return Response({
                    'error': f"Failed to apply bucket policy: {output.decode().strip()}"
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Persist the state in the database
            addon.is_bucket_public = is_public
            addon.save(update_fields=['is_bucket_public'])

            return Response({
                'status': 'success',
                'is_public': is_public,
                'message': f"Bucket access set to {'public' if is_public else 'private'}."
            })
        except Exception as e:
            logger.error(f"Error toggling MinIO public access for {addon.id}: {e}")
            return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='metrics')
    def metrics(self, request, pk=None):
        """Return real addon container metrics backed by Prometheus (with fallbacks)."""
        addon = self.get_object()
        duration = request.query_params.get('duration', '1h')
        try:
            from apps.deployments.metrics.adapter import MetricsAdapter
            adapter = MetricsAdapter()
            return Response(adapter.get_addon_metrics(addon, duration))
        except Exception as exc:
            logger.error("Addon metrics failed for %s: %s", addon.id, exc, exc_info=True)
            return Response({
                'cpu': [],
                'memory': [],
                'network': [],
                'disk': [],
                'current': {
                    'cpu_percent': 0.0,
                    'memory_usage': 0.0,
                    'memory_limit': 0.0,
                    'memory_percent': 0.0,
                    'network_rx_kb': 0.0,
                    'network_tx_kb': 0.0,
                },
                'source': 'unavailable',
                'error': 'Metrics temporarily unavailable',
            }, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Unified addons + bundles endpoint
# ---------------------------------------------------------------------------

from rest_framework.decorators import api_view, permission_classes


def _redact_connection_url(url: str) -> str:
    """Mask the password component of a connection URL, keeping the key string-typed."""
    if not url:
        return ''
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        if parsed.password:
            masked_netloc = parsed.netloc.replace(f":{parsed.password}@", ':*****@')
            return urlunparse(parsed._replace(netloc=masked_netloc))
    except Exception:
        pass
    return url


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def service_addons_unified(request, service_id) -> Response:
    """Return all addons AND bundle components for a service in one list.

    This powers the Addons tab in the frontend.  Each item has a
    ``_type`` field set to ``"addon"`` or ``"bundle_component"`` so the
    frontend can distinguish them while rendering a single list.
    """
    try:
        service = Service.objects.filter(
            get_team_q_filter(request.user),
        ).get(pk=service_id)
    except Service.DoesNotExist:
        return Response({'error': 'Service not found'}, status=404)

    result = []

    # Standard addons
    for addon in Addon.objects.filter(service=service).exclude(
        status=Addon.Status.DELETED,
    ):
        result.append({
            '_type': 'addon',
            'id': str(addon.id),
            'name': addon.name,
            'addon_type': addon.addon_type,
            'status': addon.status,
            # Redacted: never leak DB credentials in a bulk list endpoint.
            'connection_url': _redact_connection_url(addon.connection_url or ''),
            'public_domain': addon.public_domain or '',
            'created_at': addon.created_at.isoformat() if hasattr(addon, 'created_at') and addon.created_at else None,
        })

    # Bundle components
    from apps.deployments.models.bundles import Bundle
    for bundle in Bundle.objects.filter(service=service).prefetch_related('components').exclude(
        status=Bundle.Status.DELETED,
    ):
        for comp in bundle.components.all():
            result.append({
                '_type': 'bundle_component',
                'id': str(comp.id),
                'name': f"{comp.name} ({bundle.name})",
                'bundle_name': bundle.name,
                'addon_type': f"BUNDLE_{bundle.name.upper()}",
                'status': comp.status,
                'source_type': comp.source_type,
                'connection_url': _redact_connection_url(comp.connection_url or ''),
                'image': comp.image,
                'repo': comp.repo,
                'bundle_id': str(bundle.id),
                'created_at': comp.created_at.isoformat() if hasattr(comp, 'created_at') and comp.created_at else None,
            })

    return Response(result)


from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def toggle_bucket_public_api(request, pk) -> Response:
    """Standalone API function with maximal diagnostic logging."""
    if settings.DEBUG:
        logger.info("toggle_bucket_public_api entered for pk=%s, method=%s", pk, request.method)
    try:
        from django.db.models import Q

        from apps.deployments.models.addons import Addon
        # SECURITY: scope the lookup to addons the caller can access
        # (mirrors AddonViewSet.get_queryset). Without this, any
        # authenticated user could flip any tenant's MinIO bucket
        # public.
        try:
            addon = Addon.objects.filter(
                Q(service__owner=request.user) |
                Q(service__project__team__members__user=request.user)
            ).distinct().get(pk=pk)
        except Addon.DoesNotExist:
            if settings.DEBUG:
                logger.warning("Addon %s not accessible to user %s", pk, request.user.id)
            return Response({'error': f'Addon {pk} not found'}, status=404)

        if settings.DEBUG:
            logger.info("Found addon %s (%s)", addon.name, addon.addon_type)
        if addon.addon_type != 'MINIO':
            return Response({'error': f'Addon type {addon.addon_type} does not support bucket toggling'}, status=400)

        # Reuse the existing ViewSet method logic by passing the addon directly
        # or just reimplementing the core logic here for absolute safety
        from apps.cloud.docker_client import get_docker_client
        is_public = request.data.get('is_public', False)
        bucket_name = "default-bucket"
        policy = "public" if is_public else "none"

        client = get_docker_client()
        container = None
        addon_uuid = str(addon.id)

        # Canonical container name first (matches the provisioner naming
        # convention), then a UUID scan as a fallback. `addon.name` is only a
        # network alias — never a container name.
        try:
            container = client.containers.get(f"smsly-addon-minio-{addon_uuid}")
        except Exception:
            container = None

        if not container:
            # ── UUID DISCOVERY: Search for the Addon ID in the container names ──
            all_containers = client.containers.list()
            if settings.DEBUG:
                logger.info("Scanning %s containers for ID: %s", len(all_containers), addon_uuid)

            for c in all_containers:
                # Match by UUID (case-insensitive substring)
                if addon_uuid.lower() in c.name.lower():
                    container = c
                    if settings.DEBUG:
                        logger.info("Found match by UUID in name: %s", c.name)
                    break

        if not container:
            if settings.DEBUG:
                logger.error("FAILED: Could not find container for Addon %s after full scan", addon_uuid)
            return Response({'error': 'MinIO container not found for this addon'}, status=404)

        cmd = ['mc', 'anonymous', 'set', policy, f'myminio/{bucket_name}']
        exit_code, output = container.exec_run(cmd)
        if exit_code != 0:
            return Response({'error': output.decode().strip()}, status=500)

        addon.is_bucket_public = is_public
        addon.save(update_fields=['is_bucket_public'])
        return Response({'status': 'success', 'is_public': is_public})
    except Addon.DoesNotExist:
        return Response({'error': 'Addon not found'}, status=404)
    except Exception as e:
        # SECURITY: log the full error server-side but don't echo it
        # to the caller (info-leak / SSRF probe risk).
        logger.error("Standalone toggle failed: %s", e, exc_info=True)
        return Response({'error': 'Internal error toggling bucket.'}, status=500)
