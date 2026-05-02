"""Views Addons module."""
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.cloud.docker_client import get_docker_client
import re
from django.db.models import Q
from .models_addons import Addon
from .models import Service, EnvironmentVariable
import logging

logger = logging.getLogger(__name__)


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
        from .models_addons import Backup
        model = Backup
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
        """Filter addons to only those belonging to the user's services."""
        return self.queryset.filter(
            Q(service__owner=self.request.user) | Q(service__owner__isnull=True)
        )

    def perform_create(self, serializer):
        # SECURITY: Verify user owns the service before creating addon
        service = serializer.validated_data.get('service')
        if service and service.owner and service.owner != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Access denied to this service.")

        addon = serializer.save()

        # Auto-expose new addons by default
        if not addon.public_domain:
            from .models_core import Service
            base_domain = Service.default_public_base_domain()
            short_id = str(addon.id).split('-')[0]
            slug = re.sub(r'[^a-z0-9]+', '-', addon.addon_type.lower()).strip('-')
            addon.public_domain = f"addon-{slug}-{short_id}.{base_domain}"
            addon.save(update_fields=['public_domain'])

        # Trigger async provisioning via Celery (uses Docker-native
        # provisioner)
        from .tasks import provision_addon_task
        provision_addon_task.delay(addon_id=str(addon.id))


    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(
            {
                "ok": True,
                "status": "deletion_pending",
                "message": "Deletion has started.",
                "resource_id": str(instance.id),
            },
            status=status.HTTP_202_ACCEPTED
        )

    def perform_destroy(self, instance):
        """Set status to pending and queue async deletion."""
        from .tasks import delete_addon_task
        from .models_addons import Addon
        
        instance.status = Addon.Status.DELETION_PENDING
        instance.save(update_fields=['status'])

        delete_addon_task.delay(str(instance.id))

    @action(detail=True, methods=['post'], url_path='retry-delete')
    def retry_delete(self, request, pk=None):
        instance = self.get_object()
        from .models_addons import Addon
        if instance.status not in [Addon.Status.DELETION_FAILED, Addon.Status.DELETION_PENDING]:
            return Response({"error": "Addon is not in a failed or pending deletion state."}, status=status.HTTP_400_BAD_REQUEST)

        instance.status = Addon.Status.DELETION_PENDING
        instance.save(update_fields=['status'])
        from .tasks import delete_addon_task
        delete_addon_task.delay(str(instance.id))

        return Response({"message": "Retry cleanup initiated."}, status=status.HTTP_202_ACCEPTED)

    def perform_update(self, serializer):
        # Allow updating properties like public_domain
        service = serializer.instance.service
        if service and service.owner and service.owner != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Access denied to this service.")

        addon = serializer.save()
        # If public_domain changed, re-provision to update proxy labels
        if 'public_domain' in serializer.validated_data:
            from .tasks import provision_addon_task
            provision_addon_task.delay(str(addon.id))

    @action(detail=True, methods=['post'])
    def expose(self, request, pk=None):
        """Auto-generate and assign a public domain for this addon."""
        addon = self.get_object()
        from .models_core import Service
        base_domain = Service.default_public_base_domain()

        # Format: minio-uuid.domain.com
        short_id = str(addon.id).split('-')[0]
        slug = re.sub(r'[^a-z0-9]+', '-', addon.addon_type.lower()).strip('-')
        generated_domain = f"addon-{slug}-{short_id}.{base_domain}"

        addon.public_domain = generated_domain
        addon.save(update_fields=['public_domain'])

        from .tasks import provision_addon_task
        provision_addon_task.delay(str(addon.id))

        return Response({'public_domain': generated_domain})

    @action(detail=True, methods=['post'])
    def reprovision(self, request, pk=None):
        """Manually trigger re-provisioning to update labels or network configuration."""
        addon = self.get_object()
        from .tasks import provision_addon_task
        provision_addon_task.delay(str(addon.id))
        return Response({'status': 'reprovision_started'})

    @action(detail=True, methods=['post'])
    def deprovision(self, request, pk=None):
        """Delete addon container and remove from service."""
        addon = self.get_object()
        from .tasks import deprovision_addon_task
        deprovision_addon_task.delay(addon_id=str(addon.id))
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
        from services.addon_provisioner import addon_provisioner

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

    @action(detail=True, methods=['post'])
    def backup(self, request, pk=None):
        """Trigger a backup for this addon."""
        addon = self.get_object()
        from .tasks import backup_addon_task
        task = backup_addon_task.delay(addon_id=str(addon.id))
        return Response({'status': 'backup_started', 'task_id': task.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore a backup to this addon."""
        addon = self.get_object()
        backup_id = request.data.get('backup_id')
        if not backup_id:
            return Response({'error': 'backup_id required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Verify backup belongs to addon
        from .models_addons import Backup
        if not Backup.objects.filter(id=backup_id, addon=addon).exists():
            return Response({'error': 'Backup not found for this addon'}, status=status.HTTP_404_NOT_FOUND)

        from .tasks import restore_addon_task
        task = restore_addon_task.delay(backup_id=backup_id)
        return Response({'status': 'restore_started', 'task_id': task.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def backups(self, request, pk=None):
        """List backups for this addon."""
        addon = self.get_object()
        from .models_addons import Backup
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
        
        from .models_addons import Backup
        try:
            backup = Backup.objects.get(id=backup_id, addon=addon)
        except Backup.DoesNotExist:
            return Response({'error': 'Backup not found'}, status=status.HTTP_404_NOT_FOUND)
            
        import os
        from django.http import FileResponse
        if not os.path.exists(backup.file_path):
            return Response({'error': 'File not found on disk'}, status=status.HTTP_404_NOT_FOUND)

        # Security: ensure the file path is within the expected backups directory
        from django.conf import settings as django_settings
        backups_root = os.path.realpath(os.path.join(django_settings.BASE_DIR, 'backups'))
        real_path = os.path.realpath(backup.file_path)
        if not real_path.startswith(backups_root):
            logger.warning("Blocked backup download path traversal: %s", backup.file_path)
            return Response({'error': 'Invalid backup path'}, status=status.HTTP_403_FORBIDDEN)

        response = FileResponse(open(backup.file_path, 'rb'), as_attachment=True, filename=os.path.basename(backup.file_path))
        return response

    @action(detail=True, methods=['post'])
    def toggle_bucket_public(self, request, pk=None):
        """Toggle MinIO bucket public read access policy."""
        addon = self.get_object()

        if addon.addon_type != 'MINIO':
            return Response({'error': 'Only MinIO addons support public bucket toggling.'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.cloud.docker_client import get_docker_client
        is_public = request.data.get('is_public', False)

        # The container name for an addon is its name
        container_name = addon.name
        bucket_name = "default-bucket"

        policy = "public" if is_public else "none"

        try:
            client = get_docker_client()
            # Robust container lookup (handles platform/compose prefixes)
            try:
                container = client.containers.get(container_name)
            except Exception:
                # Fallback: search for containers containing the addon name
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


from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def toggle_bucket_public_api(request, pk):
    """Standalone API function with maximal diagnostic logging."""
    logger.info(f"[MINIO_DEBUG] toggle_bucket_public_api entered for pk={pk}, method={request.method}")
    try:
        from apps.deployments.models_addons import Addon
        try:
            addon = Addon.objects.get(pk=pk)
        except Exception as db_err:
            logger.warning(f"[MINIO_DEBUG] Addon {pk} not found in DB: {db_err}")
            return Response({'error': f'Addon {pk} not found in database'}, status=404)
        
        logger.info(f"[MINIO_DEBUG] Found addon {addon.name} ({addon.addon_type})")
        if addon.addon_type != 'MINIO':
            return Response({'error': f'Addon type {addon.addon_type} does not support bucket toggling'}, status=400)
        
        # Reuse the existing ViewSet method logic by passing the addon directly 
        # or just reimplementing the core logic here for absolute safety
        from apps.cloud.docker_client import get_docker_client
        is_public = request.data.get('is_public', False)
        container_name = addon.name
        bucket_name = "default-bucket"
        policy = "public" if is_public else "none"

        client = get_docker_client()
        container = None
        addon_uuid = str(addon.id)
        
        # ── UUID DISCOVERY: Search for the Addon ID in the container names ──
        all_containers = client.containers.list()
        logger.info(f"[MINIO_DEBUG] Scanning {len(all_containers)} containers for ID: {addon_uuid}")
        
        for c in all_containers:
            # Match by UUID (case-insensitive substring)
            if addon_uuid.lower() in c.name.lower():
                container = c
                logger.info(f"[MINIO_DEBUG] Found match by UUID in name: {c.name}")
                break

        if not container:
            # Fallback to name scan if UUID fails (legacy or different prefix)
            container_name = addon.name
            for c in all_containers:
                if container_name.lower() in c.name.lower():
                    container = c
                    logger.info(f"[MINIO_DEBUG] Found match by legacy name fallback: {c.name}")
                    break

        if not container:
            logger.error(f"[MINIO_DEBUG] FAILED: Could not find container for Addon {addon_uuid} after full scan")
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
        logger.error(f"Standalone toggle failed: {e}")
        return Response({'error': str(e)}, status=500)
