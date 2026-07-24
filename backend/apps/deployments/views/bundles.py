"""Bundle API views.

Exposes the same lifecycle operations as :class:`AddonViewSet` for
bundle components so they appear and behave identically in the
service Addons tab.

Endpoints mirror the standard addon API:
- ``GET    /bundles/``                    — list bundles
- ``GET    /bundles/{id}/``               — retrieve bundle
- ``POST   /bundles/{id}/reprovision/``   — rebuild & restart
- ``POST   /bundles/{id}/deprovision/``   — tear down
- ``GET    /bundles/{id}/status_check/``  — container status
- ``GET    /bundles/{id}/logs/``          — container logs
- ``GET    /bundles/{id}/metrics/``       — container metrics
- ``GET    /bundles/{id}/network_check/`` — verify network
- ``POST   /bundles/{id}/backup/``        — create backup
- ``POST   /bundles/{id}/restore/``       — restore backup
- ``GET    /bundles/{id}/backups/``       — list backups
- ``GET    /bundles/{id}/download_backup/``— download backup file
"""
import logging
import os

from django.conf import settings
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models import Service
from apps.deployments.models.bundles import Bundle, BundleBackup, BundleComponent
from apps.teams.permissions import (
    assert_can_delete,
    assert_can_write,
    get_team_q_filter,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

class BundleComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = BundleComponent
        fields = [
            'id', 'name', 'source_type', 'image', 'repo', 'branch',
            'status', 'container_name', 'connection_url', 'ports',
            'health_status',
        ]
        read_only_fields = [
            'status', 'container_name', 'connection_url', 'health_status',
        ]


class BundleSerializer(serializers.ModelSerializer):
    components = BundleComponentSerializer(many=True, read_only=True)
    service_name = serializers.CharField(source='service.name', read_only=True)

    class Meta:
        model = Bundle
        fields = [
            'id', 'service', 'service_name', 'name', 'network', 'status',
            'grid_addons_hash', 'components', 'created_at',
        ]
        read_only_fields = ['service', 'status', 'network', 'grid_addons_hash']


class BundleBackupSerializer(serializers.ModelSerializer):
    class Meta:
        model = BundleBackup
        fields = [
            'id', 'component', 'status', 'size_bytes',
            'created_at', 'completed_at', 'error_message',
        ]
        read_only_fields = [
            'status', 'size_bytes', 'created_at', 'completed_at', 'error_message',
        ]


# ---------------------------------------------------------------------------
# ViewSet
# ---------------------------------------------------------------------------

class BundleViewSet(viewsets.ModelViewSet):
    """CRUD + lifecycle operations for custom infrastructure bundles."""
    queryset = Bundle.objects.select_related('service').prefetch_related('components').order_by('-created_at')
    serializer_class = BundleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        allowed_services = Service.objects.filter(
            get_team_q_filter(self.request.user),
        )
        qs = self.queryset.filter(service__in=allowed_services)
        if self.request.user.is_superuser:
            qs = qs | self.queryset.filter(service__owner__isnull=True)

        # Optional filter by service
        service_id = self.request.query_params.get('service_id')
        if service_id:
            qs = qs.filter(service_id=service_id)

        return qs.distinct()

    def perform_create(self, serializer):
        service = serializer.validated_data.get('service')
        if service:
            assert_can_write(self.request.user, service, action='create bundle')
        serializer.save()

    def perform_destroy(self, instance):
        assert_can_delete(self.request.user, instance.service)
        instance.status = Bundle.Status.DELETION_PENDING
        instance.save(update_fields=['status'])
        from ..tasks.deployment.tasks_bundles import delete_bundle_task
        delete_bundle_task.delay(str(instance.id))

    # -------------------------------------------------------------------
    # Lifecycle actions
    # -------------------------------------------------------------------

    @action(detail=True, methods=['post'])
    def reprovision(self, request, pk=None):
        """Rebuild and restart all components in the bundle."""
        bundle = self.get_object()
        assert_can_write(request.user, bundle.service, action='reprovision bundle')

        bundle.status = Bundle.Status.PROVISIONING
        bundle.save(update_fields=['status'])

        from ..tasks.deployment.tasks_bundles import reprovision_bundle_task
        reprovision_bundle_task.delay(str(bundle.id))

        return Response(
            {'status': 'reprovision_started'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'])
    def deprovision(self, request, pk=None):
        """Stop and remove all bundle infrastructure."""
        bundle = self.get_object()
        assert_can_delete(request.user, bundle.service)

        bundle.status = Bundle.Status.DELETION_PENDING
        bundle.save(update_fields=['status'])

        from ..tasks.deployment.tasks_bundles import deprovision_bundle_task
        deprovision_bundle_task.delay(str(bundle.id))

        return Response(
            {'status': 'deprovisioning'},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['get'], url_path='status_check')
    def status_check(self, request, pk=None):
        """Check status of all components in the bundle."""
        bundle = self.get_object()
        from apps.addons.services.bundle_provisioner import bundle_provisioner

        try:
            result = bundle_provisioner.get_status(
                bundle.name, str(bundle.service.id),
            )
            return Response({
                'status': bundle.status,
                'running': result.get('running', False),
                'components': result.get('components', []),
            })
        except Exception as exc:
            logger.error("Status check failed for bundle %s: %s", pk, exc)
            return Response({
                'status': bundle.status,
                'error': str(exc),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def logs(self, request, pk=None):
        """Get logs from bundle components.

        Query params:
            component — specific component name (optional)
            tail — max lines (default 200)
        """
        bundle = self.get_object()
        component_name = request.query_params.get('component')
        tail = int(request.query_params.get('tail', 200))

        from apps.addons.services.bundle_provisioner import bundle_provisioner
        try:
            log_text = bundle_provisioner.get_logs(
                bundle.name, str(bundle.service.id),
                component_name=component_name, tail=min(tail, 2000),
            )
            return Response({
                'bundle': bundle.name,
                'component': component_name,
                'logs': log_text,
            })
        except Exception as exc:
            logger.error("Failed to fetch logs for bundle %s: %s", pk, exc)
            return Response({
                'bundle': bundle.name,
                'logs': '',
                'message': f'Could not fetch logs: {exc}',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def metrics(self, request, pk=None):
        """Get container metrics for all components."""
        bundle = self.get_object()
        from apps.addons.services.bundle_provisioner import bundle_provisioner

        try:
            result = bundle_provisioner.get_metrics(
                bundle.name, str(bundle.service.id),
            )
            return Response(result)
        except Exception as exc:
            logger.error("Metrics failed for bundle %s: %s", pk, exc)
            return Response({
                'components': [],
                'error': 'Metrics temporarily unavailable',
            }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='network_check')
    def network_check(self, request, pk=None):
        """Verify and repair the bundle's Docker network."""
        bundle = self.get_object()
        from apps.addons.services.bundle_provisioner import bundle_provisioner

        try:
            network = bundle_provisioner.ensure_network(
                bundle.name, str(bundle.service.id),
                network_name=bundle.network or None,
            )
            return Response({
                'bundle': bundle.name,
                'network': network,
                'status': bundle.status,
            })
        except Exception as exc:
            logger.error("Network check failed for bundle %s: %s", pk, exc)
            return Response({
                'bundle': bundle.name,
                'error': str(exc),
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # -------------------------------------------------------------------
    # Backup / Restore
    # -------------------------------------------------------------------

    @action(detail=True, methods=['post'])
    def backup(self, request, pk=None):
        """Trigger backup for a bundle component.

        Body: ``{"component": "kamailio"}``
        """
        bundle = self.get_object()
        component_name = request.data.get('component')
        if not component_name:
            return Response(
                {'error': 'component name required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            component = BundleComponent.objects.get(
                bundle=bundle, name=component_name,
            )
        except BundleComponent.DoesNotExist:
            return Response(
                {'error': f'Component {component_name} not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..tasks.deployment.tasks_bundles import backup_bundle_component_task
        task = backup_bundle_component_task.delay(str(component.id))
        return Response(
            {'status': 'backup_started', 'task_id': task.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore a backup to a bundle component.

        Body: ``{"backup_id": "uuid"}``
        """
        bundle = self.get_object()
        backup_id = request.data.get('backup_id')
        if not backup_id:
            return Response(
                {'error': 'backup_id required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            backup = BundleBackup.objects.get(
                id=backup_id, component__bundle=bundle,
            )
        except BundleBackup.DoesNotExist:
            return Response(
                {'error': 'Backup not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        from ..tasks.deployment.tasks_bundles import restore_bundle_component_task
        task = restore_bundle_component_task.delay(str(backup.id))
        return Response(
            {'status': 'restore_started', 'task_id': task.id},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['get'])
    def backups(self, request, pk=None):
        """List backups for all components in the bundle."""
        bundle = self.get_object()
        backups = BundleBackup.objects.filter(
            component__bundle=bundle,
        ).order_by('-created_at')
        serializer = BundleBackupSerializer(backups, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='download_backup')
    def download_backup(self, request, pk=None):
        """Download a backup file."""
        bundle = self.get_object()
        backup_id = request.query_params.get('backup_id')
        if not backup_id:
            return Response(
                {'error': 'backup_id required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            backup = BundleBackup.objects.get(
                id=backup_id, component__bundle=bundle,
            )
        except BundleBackup.DoesNotExist:
            return Response(
                {'error': 'Backup not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not os.path.exists(backup.file_path):
            return Response(
                {'error': 'File not found on disk'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Security: ensure path is within backups directory
        backups_root = os.path.realpath(
            os.path.join(settings.BASE_DIR, 'backups'),
        )
        real_path = os.path.realpath(backup.file_path)
        if not real_path.startswith(backups_root):
            logger.warning(
                "Blocked backup download path traversal: %s", backup.file_path,
            )
            return Response(
                {'error': 'Invalid backup path'},
                status=status.HTTP_403_FORBIDDEN,
            )

        import re

        from .addons import _ClosingFileResponse
        backup_file = open(backup.file_path, 'rb')
        response = _ClosingFileResponse(backup_file, as_attachment=True)
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', os.path.basename(backup.file_path))
        response['Content-Disposition'] = f'attachment; filename="{safe_name}"'
        return response

    # -------------------------------------------------------------------
    # Credentials
    # -------------------------------------------------------------------

    @action(detail=True, methods=['get'])
    def credentials(self, request, pk=None):
        """Return parsed connection credentials for all components."""
        bundle = self.get_object()
        components = BundleComponent.objects.filter(
            bundle=bundle, status='ACTIVE',
        )
        result = {}
        for comp in components:
            result[comp.name] = comp.parsed_credentials
        return Response(result)
