import os
import posixpath
import uuid
import mimetypes
from django.http import StreamingHttpResponse
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets, permissions, serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.response import Response
from .models_storage import Volume
from .models import Service
from apps.cloud.adapters.local import LocalAdapter


class VolumeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Volume
        fields = '__all__'
        read_only_fields = ('service',)


class VolumeViewSet(viewsets.ModelViewSet):
    serializer_class = VolumeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def _validated_service_uuid(self):
        service_pk = self.kwargs.get('service_pk')
        if not service_pk:
            return None
        try:
            return uuid.UUID(str(service_pk))
        except (ValueError, TypeError, AttributeError):
            return None

    def get_queryset(self):
        service_uuid = self._validated_service_uuid()
        if service_uuid:
            return Volume.objects.filter(
                service_id=service_uuid,
                service__owner=self.request.user,
            )
        return Volume.objects.none()

    def perform_create(self, serializer):
        service_uuid = self._validated_service_uuid()
        if not service_uuid:
            raise NotFound("Service not found.")
        try:
            service = Service.objects.get(pk=service_uuid)
        except (Service.DoesNotExist, DjangoValidationError):
            raise NotFound("Service not found.")
        # M-1 fix: verify the requesting user owns this service
        if service.owner != self.request.user:
            raise PermissionDenied("You do not own this service.")
        serializer.save(service=service)

    @action(detail=True, methods=['get'])
    def browse(self, request, pk=None, service_pk=None):
        """
        List files in the volume.
        Uses docker exec ls -la on the active container.
        """
        volume = self.get_object()
        path = request.query_params.get('path', volume.mount_path)

        # C-1 fix: normalize path to prevent traversal and command injection.
        # posixpath.normpath collapses ".." and "." sequences.
        path = posixpath.normpath(path)

        # Reject any path that doesn't start with the volume mount path
        mount = posixpath.normpath(volume.mount_path)
        if not (path == mount or path.startswith(mount + "/")):
            return Response({'error': 'Invalid path'},
                            status=status.HTTP_403_FORBIDDEN)

        service = volume.service
        latest_deploy = service.deployments.filter(status='ACTIVE').first()

        if not latest_deploy or not latest_deploy.container_id:
            return Response({'error': 'No active container'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        adapter = LocalAdapter()
        if adapter.docker_client:
            try:
                container = adapter.docker_client.containers.get(
                    latest_deploy.container_id)
                # C-1 fix: use argument-list form to prevent shell injection
                exit_code, output = container.exec_run(
                    ["ls", "-la", path], user="root")

                if exit_code != 0:
                    return Response({'error': 'Failed to list directory', 'details': output.decode(
                    )}, status=status.HTTP_400_BAD_REQUEST)

                # Parse ls -la output into JSON
                files = []
                lines = output.decode('utf-8').splitlines()
                # Skip total line
                if lines and lines[0].startswith('total'):
                    lines = lines[1:]

                for line in lines:
                    parts = line.split()
                    if len(parts) >= 9:
                        files.append({
                            'permissions': parts[0],
                            'user': parts[2],
                            'size': parts[4],
                            'date': f"{parts[5]} {parts[6]} {parts[7]}",
                            'name': " ".join(parts[8:])
                        })

                return Response({'path': path, 'files': files})

            except Exception as e:
                return Response({'error': str(e)},
                                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'files': []})  # Mock if no docker

    @action(detail=True, methods=['post'], url_path='delete-file')
    def delete_file(self, request, pk=None, service_pk=None):
        """Delete a file or directory in the volume."""
        volume = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        # Security normalization
        path = posixpath.normpath(path)
        mount = posixpath.normpath(volume.mount_path)
        if not path.startswith(mount + "/"):
            return Response({'error': 'Invalid path'}, status=status.HTTP_403_FORBIDDEN)

        latest_deploy = volume.service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy or not latest_deploy.container_id:
            return Response({'error': 'No active container'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        try:
            container = client.containers.get(latest_deploy.container_id)
            exit_code, output = container.exec_run(["rm", "-rf", path], user="root")
            if exit_code != 0:
                return Response({'error': 'Delete failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Deleted successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='download-file')
    def download_file(self, request, pk=None, service_pk=None):
        """Download a file from the volume."""
        volume = self.get_object()
        path = request.query_params.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        # Security normalization
        path = posixpath.normpath(path)
        mount = posixpath.normpath(volume.mount_path)
        if not path.startswith(mount + "/"):
            return Response({'error': 'Invalid path'}, status=status.HTTP_403_FORBIDDEN)

        latest_deploy = volume.service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy or not latest_deploy.container_id:
            return Response({'error': 'No active container'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        try:
            container = client.containers.get(latest_deploy.container_id)
            # Use get_archive for streaming
            bits, stat = container.get_archive(path)
            
            # Note: get_archive returns a tar stream. For simple downloads we might want a single file.
            # However, for simplicity and supporting directories, tar is fine.
            # If it's a single file, the user might expect the raw bytes.
            
            response = StreamingHttpResponse(bits, content_type='application/x-tar')
            filename = os.path.basename(path) + ".tar"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='mkdir')
    def mkdir(self, request, pk=None, service_pk=None):
        """Create a directory in the volume."""
        volume = self.get_object()
        path = request.data.get('path')
        if not path:
            return Response({'error': 'Path required'}, status=status.HTTP_400_BAD_REQUEST)

        path = posixpath.normpath(path)
        mount = posixpath.normpath(volume.mount_path)
        if not (path == mount or path.startswith(mount + "/")):
            return Response({'error': 'Invalid path'}, status=status.HTTP_403_FORBIDDEN)

        latest_deploy = volume.service.deployments.filter(status='ACTIVE').first()
        if not latest_deploy or not latest_deploy.container_id:
            return Response({'error': 'No active container'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        from apps.cloud.docker_client import get_docker_client
        client = get_docker_client()
        try:
            container = client.containers.get(latest_deploy.container_id)
            exit_code, output = container.exec_run(["mkdir", "-p", path], user="root")
            if exit_code != 0:
                return Response({'error': 'Mkdir failed', 'details': output.decode()}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'Created successfully'})
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
